"""Synthetic paid-semantic regressions: reservation, fences, namespaces, fusion.

Everything here uses authored fixtures, an injected fake gateway and isolated
databases. No provider, credential, live model or network access is used.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from navox.ai.catalog import catalog_template
from navox.ai.context import ContextDenied
from navox.ai.embedding_contracts import EmbeddingOutput
from navox.ai.foundation.adapter import ErrorCode, ProviderResponse
from navox.ai.foundation.contracts import (
    Capability,
    FinishReason,
    JSONDocument,
    Profile,
    Provider,
    ProviderGrant,
    Sensitivity,
    TaskType,
    Usage,
    VersionedRef,
)
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.foundation.registry import ModelDefinition, ModelRef
from navox.ai.providers import AdapterFailure, HTTPAdapter
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.core.settings import Settings
from navox.db.ai_registry import AIEvaluationRun, AIProfileAssignment, AITaskRun
from navox.db.knowledge import (
    KnowledgeChunk,
    KnowledgeEmbedding,
    KnowledgeEmbeddingRequest,
    KnowledgeResource,
    KnowledgeResourceIndex,
    KnowledgeResourcePermission,
)
from navox.db.models import ConnectorResource
from navox.knowledge.embeddings import (
    EMBEDDING_VERSION,
    MAX_CHUNK_INDEX,
    EmbeddingBinding,
    EmbeddingRequest,
    EmbeddingVector,
    KnowledgeContext,
    RegisteredEmbeddingGateway,
    SemanticDisabled,
    SemanticQuotaExceeded,
    SemanticRejected,
    SemanticRequestReplay,
    SemanticUnavailable,
    _embedding_task,
    embed_resource,
    normalize_vector,
    reserve_attempt,
    semantic_search,
    store_embedding,
)
from navox.knowledge.indexing import backfill_workspace
from navox.knowledge.retrieval import parse_stored_vector
from navox.knowledge.search_contracts import (
    SEMANTIC_REASON_MISSING,
    SEMANTIC_REASON_NAMESPACE,
    SEMANTIC_REASON_OUTDATED,
    SEMANTIC_REASON_PARTIAL,
    SEMANTIC_REASON_UNAVAILABLE,
    RetrieverMode,
    SemanticSearchRequest,
)
from tests.knowledge_search_support import (
    NOW,
    World,
    build_engine,
    factory_for,
    load_world,
    seed_world,
)

PROVIDER = "fixture"
MODEL = "fixture-embedding-v1"
REVISION = "41"


class FakeEmbeddingGateway:
    """A qualified-looking fake: no HTTP, no model, deterministic vectors."""

    def __init__(
        self,
        *,
        vector: tuple[float, ...] = (1.0, 0.0, 0.0),
        provider: str = PROVIDER,
        model: str = MODEL,
        registry_revision: str = REVISION,
        eligible: bool = True,
        cost_micros: int | None = 200,
        during: object = None,
    ) -> None:
        self.vector = vector
        self.provider, self.model = provider, model
        self.registry_revision = registry_revision
        self.eligible_result = eligible
        self.cost_micros = cost_micros
        self.during = during
        self.calls: list[EmbeddingRequest] = []
        self.eligibility_checks = 0

    async def eligible(self, *, workspace_id, user_id, sensitivity, text_length) -> bool:
        self.eligibility_checks += 1
        return self.eligible_result

    async def embed(self, request: EmbeddingRequest) -> EmbeddingVector | None:
        self.calls.append(request)
        if self.during is not None:
            await self.during(request)  # type: ignore[operator]
        if not self.eligible_result:
            return None
        return EmbeddingVector(
            provider=self.provider,
            model=self.model,
            registry_revision=self.registry_revision,
            embedding_version=EMBEDDING_VERSION,
            vector=self.vector,
            cost_micros=self.cost_micros,
        )


@pytest_asyncio.fixture
async def search_engine(tmp_path: object) -> AsyncEngine:
    database = await build_engine(tmp_path)
    yield database.engine
    await database.dispose()


@pytest_asyncio.fixture
async def semantic_factory(
    search_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    factory = factory_for(search_engine)
    await seed_world(factory)
    return factory


def semantic_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "knowledge_enabled": True,
        "knowledge_semantic_enabled": True,
        "knowledge_semantic_hourly_quota": 20,
    }
    values.update(changes)
    return Settings(_env_file=None, app_environment="test", **values)  # type: ignore[arg-type]


async def project(factory: async_sessionmaker[AsyncSession], world: World) -> None:
    async with factory() as database:
        report = await backfill_workspace(
            database, workspace_id=world.workspace_id, user_id=world.user_id, now=NOW
        )
        assert report.outcomes
        await database.commit()


async def resource_for(
    factory: async_sessionmaker[AsyncSession], external_id: str
) -> KnowledgeResource:
    async with factory() as database:
        row = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.external_resource_id == external_id)
        )
        assert row is not None, f"missing projected {external_id}"
        return row


async def stored_vector(
    factory: async_sessionmaker[AsyncSession],
    *,
    resource: KnowledgeResource,
    chunk_index: int = 0,
    vector: tuple[float, ...] = (1.0, 0.0, 0.0),
    provider: str = PROVIDER,
    model: str = MODEL,
    registry_revision: str = REVISION,
) -> None:
    """Write one vector through the real writer, inside the exact namespace."""
    async with factory() as database:
        assert resource.source_resource_id is not None
        index_hash = await database.scalar(
            select(ConnectorResource.content_hash).where(
                ConnectorResource.id == resource.source_resource_id
            )
        )
        assert index_hash
        await store_embedding(
            database,
            workspace_id=resource.workspace_id,
            resource_id=resource.id,
            chunk_index=chunk_index,
            source_content_hash=index_hash,
            sensitivity=resource.sensitivity,
            vector=EmbeddingVector(
                provider=provider,
                model=model,
                registry_revision=registry_revision,
                embedding_version=EMBEDDING_VERSION,
                vector=vector,
                cost_micros=100,
            ),
            now=NOW,
        )


async def run_semantic(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    gateway: FakeEmbeddingGateway,
    *,
    query: str = "holiday plans",
    request_id: UUID | None = None,
    settings: Settings | None = None,
    user_id: UUID | None = None,
    record_recent: bool = False,
):
    async with factory() as database:
        return await semantic_search(
            database,
            workspace_id=world.workspace_id,
            user_id=user_id or world.user_id,
            request=SemanticSearchRequest(query=query, request_id=request_id or uuid4()),
            settings=settings or semantic_settings(),
            now=NOW,
            gateway=gateway,
            record_recent=record_recent,
        )


@pytest.mark.asyncio
async def test_semantic_ranking_fuses_with_lexical_results_and_persists_namespace(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    gateway = FakeEmbeddingGateway(vector=(1.0, 0.0, 0.0))

    response = await run_semantic(semantic_factory, world, gateway)

    assert [item.resource_id for item in response.results] == [message.id]
    assert SEMANTIC_REASON_UNAVAILABLE not in response.coverage.partial_reasons
    # Semantic and bounded structural graph retrieval both ran.
    assert RetrieverMode.SEMANTIC not in response.unavailable_modes
    assert RetrieverMode.GRAPH not in response.unavailable_modes
    assert gateway.calls and gateway.calls[0].text == "holiday plans"
    assert gateway.calls[0].binding is None
    async with semantic_factory() as database:
        attempt = await database.scalar(select(KnowledgeEmbeddingRequest))
        assert attempt is not None
        assert attempt.status == "COMPLETED"
        assert attempt.provider == PROVIDER
        assert attempt.model == MODEL
        assert attempt.registry_revision == REVISION
        assert attempt.embedding_version == EMBEDDING_VERSION
        assert attempt.dimension == 3
        assert attempt.cost_micros == 200


@pytest.mark.asyncio
async def test_private_high_similarity_vector_is_never_scored_or_read(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    gateway = FakeEmbeddingGateway(vector=(1.0, 0.0, 0.0))

    # The member sees nothing: the owner's VIEW grant does not name them.
    response = await run_semantic(semantic_factory, world, gateway, user_id=world.member_id)

    assert response.results == ()
    assert all(message.id != item.resource_id for item in response.results)
    assert str(message.id) not in response.model_dump_json()
    async with semantic_factory() as database:
        assert await database.scalar(select(func.count()).select_from(KnowledgeEmbedding)) == 1
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 1
        )


@pytest.mark.asyncio
async def test_namespace_mismatch_never_mixes_vectors(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    # The query vector comes from a different registry revision and dimension.
    gateway = FakeEmbeddingGateway(vector=(1.0, 0.0, 0.0, 0.0), registry_revision="99")

    response = await run_semantic(semantic_factory, world, gateway)

    assert response.results == ()
    assert SEMANTIC_REASON_NAMESPACE in response.coverage.partial_reasons
    async with semantic_factory() as database:
        row = await database.scalar(select(KnowledgeEmbedding))
        assert row is not None and row.dimension == 3 and row.registry_revision == REVISION


@pytest.mark.asyncio
async def test_document_embedding_stores_normalized_exact_namespace(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    gateway = FakeEmbeddingGateway(vector=(3.0, 4.0, 0.0), cost_micros=321)
    request_id = uuid4()

    async with semantic_factory() as database:
        report = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=message.id,
            request_id=request_id,
            settings=semantic_settings(),
            gateway=gateway,
            now=NOW,
        )

    assert report.status == "COMPLETED"
    assert report.key is not None and report.key.dimension == 3
    assert report.chunks_embedded == 1
    async with semantic_factory() as database:
        row = await database.scalar(select(KnowledgeEmbedding))
        assert row is not None
        assert (row.provider, row.model, row.registry_revision) == (PROVIDER, MODEL, REVISION)
        assert row.embedding_version == EMBEDDING_VERSION
        assert row.workspace_id == world.workspace_id
        assert row.sensitivity == message.sensitivity
        assert len(row.vector) == 3
        assert row.vector[0] == pytest.approx(0.6) and row.vector[1] == pytest.approx(0.8)
        attempt = await database.scalar(select(KnowledgeEmbeddingRequest))
        assert attempt is not None and attempt.status == "COMPLETED"
        assert attempt.resource_id == message.id and attempt.cost_micros == 321
        assert gateway.calls[0].purpose == "RETRIEVAL_DOCUMENT"
        assert gateway.calls[0].binding is not None


@pytest.mark.asyncio
async def test_document_zero_vector_is_rejected_and_never_stored(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    gateway = FakeEmbeddingGateway(vector=(0.0, 0.0, 0.0))

    async with semantic_factory() as database:
        with pytest.raises(SemanticUnavailable):
            await embed_resource(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_id=message.id,
                request_id=uuid4(),
                settings=semantic_settings(),
                gateway=gateway,
                now=NOW,
            )
    async with semantic_factory() as database:
        assert await database.scalar(select(func.count()).select_from(KnowledgeEmbedding)) == 0
        attempt = await database.scalar(select(KnowledgeEmbeddingRequest))
        assert attempt is not None and attempt.status == "FAILED"


@pytest.mark.asyncio
async def test_document_embedding_rejects_a_moved_source_before_reserving(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    async with semantic_factory() as database:
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        canonical.content_hash = "z" * 64
        await database.commit()
    gateway = FakeEmbeddingGateway()

    async with semantic_factory() as database:
        with pytest.raises(SemanticUnavailable):
            await embed_resource(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_id=message.id,
                request_id=uuid4(),
                settings=semantic_settings(),
                gateway=gateway,
                now=NOW,
            )
    assert gateway.calls == []
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 0
        )


@pytest.mark.asyncio
async def test_revoked_during_the_call_discards_every_vector(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")

    async def revoke(_request: EmbeddingRequest) -> None:
        async with semantic_factory() as database:
            for grant in await database.scalars(select(KnowledgeResourcePermission)):
                grant.revoked_at = NOW - timedelta(minutes=1)
            await database.commit()

    gateway = FakeEmbeddingGateway(during=revoke)
    async with semantic_factory() as database:
        report = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=message.id,
            request_id=uuid4(),
            settings=semantic_settings(),
            gateway=gateway,
            now=NOW,
        )
    assert report.status == "DISCARDED" and report.chunks_embedded == 0
    async with semantic_factory() as database:
        assert await database.scalar(select(func.count()).select_from(KnowledgeEmbedding)) == 0
        attempt = await database.scalar(select(KnowledgeEmbeddingRequest))
        assert attempt is not None and attempt.status == "DISCARDED"


@pytest.mark.asyncio
async def test_duplicate_request_id_never_buys_a_second_attempt(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    gateway = FakeEmbeddingGateway()
    request_id = uuid4()

    first = await run_semantic(semantic_factory, world, gateway, request_id=request_id)
    assert first.results
    with pytest.raises(SemanticRequestReplay):
        await run_semantic(semantic_factory, world, gateway, request_id=request_id)
    assert len(gateway.calls) == 1
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 1
        )


@pytest.mark.asyncio
async def test_hourly_quota_blocks_a_new_paid_attempt(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    gateway = FakeEmbeddingGateway()
    settings = semantic_settings(knowledge_semantic_hourly_quota=1)

    first = await run_semantic(semantic_factory, world, gateway, settings=settings)
    assert first.results
    with pytest.raises(SemanticQuotaExceeded):
        await run_semantic(semantic_factory, world, gateway, settings=settings)
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_no_eligible_model_never_reserves_or_pays(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    gateway = FakeEmbeddingGateway(eligible=False)

    response = await run_semantic(semantic_factory, world, gateway, query="quarterly budget")

    # Lexical results still answer honestly; the paid path never ran.
    assert response.results
    assert SEMANTIC_REASON_UNAVAILABLE in response.coverage.partial_reasons
    assert gateway.calls == []
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 0
        )


@pytest.mark.asyncio
async def test_credential_like_query_is_rejected_before_reserving(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    gateway = FakeEmbeddingGateway()

    with pytest.raises(SemanticRejected):
        await run_semantic(
            semantic_factory,
            world,
            gateway,
            query="sk-live-ABCDEFGHIJKLMNOPQRSTUV",
        )
    assert gateway.calls == [] and gateway.eligibility_checks == 0


@pytest.mark.asyncio
async def test_disabled_flag_refuses_the_paid_path(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    gateway = FakeEmbeddingGateway()
    with pytest.raises(SemanticDisabled):
        await run_semantic(
            semantic_factory,
            world,
            gateway,
            settings=semantic_settings(knowledge_semantic_enabled=False),
        )
    assert gateway.calls == [] and gateway.eligibility_checks == 0


@pytest.mark.asyncio
async def test_chunk_text_and_namespace_bounds_are_recorded(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One document attempt embeds bounded chunks and records a partial ceiling."""
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    async with semantic_factory() as database:
        chunk_count = await database.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.resource_id == message.id)
        )
    gateway = FakeEmbeddingGateway(cost_micros=30_000)
    async with semantic_factory() as database:
        report = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=message.id,
            request_id=uuid4(),
            settings=semantic_settings(),
            gateway=gateway,
            now=NOW,
        )
    assert chunk_count == 1
    assert report.chunks_embedded == 1
    assert report.cost_micros == 30_000
    assert report.reference is None


@pytest.mark.asyncio
async def test_attempt_rows_are_scoped_to_the_reserving_user(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    gateway = FakeEmbeddingGateway()
    request_id = uuid4()
    await run_semantic(semantic_factory, world, gateway, request_id=request_id)
    # Another member may reuse the identifier: it is owned, not global.
    async with semantic_factory() as database:
        member_world = World(
            workspace_id=world.workspace_id,
            other_workspace_id=world.other_workspace_id,
            user_id=world.member_id,
            other_user_id=world.other_user_id,
            member_id=world.member_id,
            definition_id=world.definition_id,
            connection_id=world.connection_id,
            message_id=world.message_id,
            calendar_definition_id=world.calendar_definition_id,
            calendar_connection_id=world.calendar_connection_id,
            event_id=world.event_id,
        )
    await run_semantic(semantic_factory, member_world, gateway, request_id=request_id)
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 2
        )


@pytest.mark.asyncio
async def test_datetime_bounds_of_the_quota_window_are_one_hour(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    async with semantic_factory() as database:
        database.add(
            KnowledgeEmbeddingRequest(
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                request_id=uuid4(),
                scope="QUERY",
                status="COMPLETED",
                created_at=NOW - timedelta(minutes=59),
            )
        )
        await database.commit()
    gateway = FakeEmbeddingGateway()
    with pytest.raises(SemanticQuotaExceeded):
        await run_semantic(
            semantic_factory,
            world,
            gateway,
            settings=semantic_settings(knowledge_semantic_hourly_quota=1),
        )
    assert gateway.calls == []


@pytest_asyncio.fixture
async def semantic_api(tmp_path: object):
    """A real app with an injected fake gateway; no live model is reachable."""
    from navox.api.main import create_app
    from navox.core.settings import get_settings
    from navox.db.session import get_database_session

    database = await build_engine(tmp_path)
    factory = factory_for(database.engine)
    enabled = semantic_settings()
    app = create_app()
    gateway = FakeEmbeddingGateway()
    app.state.knowledge_settings = enabled
    app.state.knowledge_embedding_gateway = gateway

    async def database_override():
        async with factory() as session:
            yield session

    def settings_override():
        return app.state.knowledge_settings

    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = settings_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "semantic-api@example.com",
                "password": "twelve-character-password",
                "display_name": "Semantic owner",
            },
        )
        assert response.status_code == 201
        yield {
            "client": client,
            "app": app,
            "gateway": gateway,
            "factory": factory,
            "origin": {"Origin": enabled.web_origin},
        }
    await database.dispose()


@pytest.mark.asyncio
async def test_get_search_never_buys_a_provider_request(semantic_api) -> None:
    client, gateway, origin = (
        semantic_api["client"],
        semantic_api["gateway"],
        semantic_api["origin"],
    )
    response = await client.get(
        "/api/v1/search",
        params={"q": "quarterly budget"},
        headers=origin,
    )
    assert response.status_code == 200
    assert SEMANTIC_REASON_UNAVAILABLE in response.json()["coverage"]["partial_reasons"]
    assert gateway.calls == [] and gateway.eligibility_checks == 0
    async with semantic_api["factory"]() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 0
        )


@pytest.mark.asyncio
async def test_post_semantic_route_reserves_one_attempt_and_replays_409(
    semantic_api,
) -> None:
    client, gateway, origin = (
        semantic_api["client"],
        semantic_api["gateway"],
        semantic_api["origin"],
    )
    request_id = str(uuid4())
    body = {"query": "quarterly budget", "request_id": request_id}
    first = await client.post("/api/v1/search/semantic", json=body, headers=origin)
    assert first.status_code == 200
    assert gateway.calls and len(gateway.calls) == 1
    replay = await client.post("/api/v1/search/semantic", json=body, headers=origin)
    assert replay.status_code == 409
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_post_semantic_route_refuses_when_its_flag_is_off(semantic_api) -> None:
    semantic_api["app"].state.knowledge_settings = semantic_settings(
        knowledge_semantic_enabled=False
    )
    response = await semantic_api["client"].post(
        "/api/v1/search/semantic",
        json={"query": "quarterly budget", "request_id": str(uuid4())},
        headers=semantic_api["origin"],
    )
    assert response.status_code == 404
    assert semantic_api["gateway"].calls == []


@pytest.mark.asyncio
async def test_post_semantic_route_rejects_credentials_and_quota(
    semantic_api,
) -> None:
    client, origin = semantic_api["client"], semantic_api["origin"]
    rejected = await client.post(
        "/api/v1/search/semantic",
        json={"query": "sk-live-ABCDEFGHIJKLMNOPQRSTUV", "request_id": str(uuid4())},
        headers=origin,
    )
    assert rejected.status_code == 422
    semantic_api["app"].state.knowledge_settings = semantic_settings(
        knowledge_semantic_hourly_quota=1
    )
    first = await client.post(
        "/api/v1/search/semantic",
        json={"query": "quarterly budget", "request_id": str(uuid4())},
        headers=origin,
    )
    assert first.status_code == 200
    exhausted = await client.post(
        "/api/v1/search/semantic",
        json={"query": "quarterly budget", "request_id": str(uuid4())},
        headers=origin,
    )
    assert exhausted.status_code == 429


class FakeAdapter:
    """Provider transport double: no HTTP, no credential, no live model."""

    def __init__(self, *, fail: bool = False, output: str = '{"vector":[0.1,0.2,0.3]}') -> None:
        self.fail, self.output = fail, output
        self.calls = []

    def capabilities(self, model) -> frozenset[Capability]:
        return frozenset({Capability.EMBEDDINGS})

    def classify_error(self, error: Exception):
        return HTTPAdapter.classify_error(self, error)  # type: ignore[arg-type]

    async def execute(self, request):
        self.calls.append(request)
        if self.fail:
            raise AdapterFailure(ErrorCode.UNAVAILABLE)
        return ProviderResponse(
            model=request.model,
            output=JSONDocument(text=self.output),
            finish_reason=FinishReason.STOP,
            usage=Usage(input_tokens=7, output_tokens=0),
        )


async def publish_embedding_registry(
    factory: async_sessionmaker[AsyncSession], *, qualified: bool = True
) -> tuple[int, PolicyRules, ModelRef]:
    """Author a synthetic registry with one optional embedding assignment."""
    reference = ModelRef(provider=Provider.OPENAI, model="fixture-embedding-v1")
    model = ModelDefinition(
        reference=reference,
        capabilities=frozenset({Capability.EMBEDDINGS}),
        context_window=32_000,
        max_output_tokens=1,
        enabled=True,
        allowed_sensitivities=frozenset(Sensitivity),
        input_cost_per_million=Decimal("0.1"),
        output_cost_per_million=Decimal("0"),
    )
    rules = PolicyRules(
        grants=(ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset(Sensitivity)),),
        max_cost=Decimal("0.05"),
    )
    async with factory() as database:
        base = catalog_template()
        profiles = tuple(
            item.model_copy(
                update={
                    "assignments": (reference,)
                    if qualified and item.profile is Profile.EMBEDDING
                    else item.assignments
                }
            )
            for item in base.profiles
        )
        registry = base.model_copy(
            update={
                "revision": 2,
                "models": (model,) if qualified else (),
                "profiles": profiles,
            }
        )
        store = RegistryStore(database)
        await store.publish(base, expected_revision=0)
        await store.publish(registry, expected_revision=1)
        if qualified:
            assignment = await database.get(
                AIProfileAssignment, (Profile.EMBEDDING.value, model_key(reference))
            )
            assert assignment is not None
            assignment.rollout_percent = 100
            evidence = EvaluationEvidence(
                profile=Profile.EMBEDDING,
                task_type=TaskType.EMBED,
                prompt=VersionedRef(name="knowledge_embedding", version="v1"),
                output_schema=VersionedRef(name="knowledge_embedding", version="v1"),
                quality=0.98,
                reliability=0.99,
                p95_latency_ms=100,
                samples=100,
                safety_passed=True,
                evaluated_at=datetime.now(UTC),
                corpus_version="synthetic.v1",
            )
            database.add(
                AIEvaluationRun(
                    model_id=model_key(reference),
                    model_digest=digest(canonical(model)),
                    registry_revision=registry.revision,
                    task_type=TaskType.EMBED.value,
                    prompt="knowledge_embedding@v1",
                    schema="knowledge_embedding@v1",
                    profile=Profile.EMBEDDING.value,
                    evidence=evidence.model_dump_json(),
                    evaluated_at=evidence.evaluated_at,
                )
            )
        await database.commit()
        return registry.revision, rules, reference


@pytest.mark.asyncio
async def test_registered_gateway_persists_the_exact_qualified_trace(
    semantic_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    """The only production path: registered selection, trace revision, one call."""
    from navox.ai import configured

    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    revision, rules, reference = await publish_embedding_registry(semantic_factory)
    adapter = FakeAdapter(output=EmbeddingOutput(vector=(0.6, 0.8, 0.0)).model_dump_json())
    monkeypatch.setattr(
        configured, "configured_adapters", lambda settings, registry: {Provider.OPENAI: adapter}
    )
    settings = Settings(
        _env_file=None,
        app_environment="test",
        knowledge_enabled=True,
        knowledge_semantic_enabled=True,
        ai_provider="automatic",
        openai_api_key=SecretStr("fixture"),
        ai_provider_policy=rules.model_dump(mode="json"),
    )
    gateway = RegisteredEmbeddingGateway(settings, factory=semantic_factory)

    async with semantic_factory() as database:
        report = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=message.id,
            request_id=uuid4(),
            settings=settings,
            gateway=gateway,
            now=NOW,
        )

    assert report.status == "COMPLETED"
    assert len(adapter.calls) == 1
    async with semantic_factory() as database:
        row = await database.scalar(select(KnowledgeEmbedding))
        assert row is not None
        assert (row.provider, row.model) == (Provider.OPENAI.value, reference.model)
        assert row.registry_revision == str(revision)
        assert row.embedding_version == EMBEDDING_VERSION
        run = await database.scalar(select(AITaskRun))
        assert run is not None and run.status == "COMPLETED"
        assert run.registry_revision == revision and run.fallback_count == 0


@pytest.mark.asyncio
async def test_registered_gateway_never_pays_a_fallback_retry(
    semantic_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    from navox.ai import configured

    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    _revision, rules, _reference = await publish_embedding_registry(semantic_factory)
    adapter = FakeAdapter(fail=True)
    monkeypatch.setattr(
        configured, "configured_adapters", lambda settings, registry: {Provider.OPENAI: adapter}
    )
    settings = Settings(
        _env_file=None,
        app_environment="test",
        knowledge_enabled=True,
        knowledge_semantic_enabled=True,
        ai_provider="automatic",
        openai_api_key=SecretStr("fixture"),
        ai_provider_policy=rules.model_dump(mode="json"),
    )
    gateway = RegisteredEmbeddingGateway(settings, factory=semantic_factory)

    async with semantic_factory() as database:
        report = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=message.id,
            request_id=uuid4(),
            settings=settings,
            gateway=gateway,
            now=NOW,
        )

    assert report.status == "UNAVAILABLE"
    assert len(adapter.calls) == 1
    async with semantic_factory() as database:
        assert await database.scalar(select(func.count()).select_from(KnowledgeEmbedding)) == 0
        rows = list(await database.scalars(select(AITaskRun)))
        assert rows and all(row.fallback_count == 0 for row in rows)


@pytest.mark.asyncio
async def test_registered_gateway_without_a_model_never_reserves(
    semantic_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    from navox.ai import configured

    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await publish_embedding_registry(semantic_factory, qualified=False)
    adapter = FakeAdapter()
    monkeypatch.setattr(
        configured, "configured_adapters", lambda settings, registry: {Provider.OPENAI: adapter}
    )
    settings = Settings(
        _env_file=None,
        app_environment="test",
        knowledge_enabled=True,
        knowledge_semantic_enabled=True,
        ai_provider="automatic",
        openai_api_key=SecretStr("fixture"),
        ai_provider_policy=PolicyRules().model_dump(mode="json"),
    )
    gateway = RegisteredEmbeddingGateway(settings, factory=semantic_factory)
    assert not await gateway.eligible(
        workspace_id=world.workspace_id,
        user_id=world.user_id,
        sensitivity=Sensitivity.PERSONAL,
        text_length=20,
    )

    async with semantic_factory() as database:
        with pytest.raises(SemanticUnavailable):
            await embed_resource(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_id=message.id,
                request_id=uuid4(),
                settings=settings,
                gateway=gateway,
                now=NOW,
            )
    assert adapter.calls == []
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 0
        )


# --- correction cycle: stale vectors, spending bounds, locks, honesty -------------


async def index_hash_for(factory: async_sessionmaker[AsyncSession], resource_id: UUID) -> str:
    async with factory() as database:
        value = await database.scalar(
            select(KnowledgeResourceIndex.source_content_hash).where(
                KnowledgeResourceIndex.resource_id == resource_id
            )
        )
        assert value
        return value


async def seed_long_message(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    *,
    external_id: str = "long-message-1",
) -> KnowledgeResource:
    """One connected resource whose retained text spans several chunks."""
    body = "\n\n".join(f"Paragraph {index} " + "budget forecast notes " * 35 for index in range(4))
    async with factory() as database:
        database.add(
            ConnectorResource(
                id=uuid4(),
                workspace_id=world.workspace_id,
                connector_connection_id=world.connection_id,
                provider="fixture",
                resource_type="communication.message",
                external_id=external_id,
                version="etag-long-1",
                canonical={
                    "subject": "Long planning memo",
                    "content": body,
                    "occurred_at": "2026-09-28T09:00:00+00:00",
                    "source_type": "communication.message",
                },
                provider_metadata={},
                source_url="https://mail.example.com/long-1",
                source_created_at=NOW - timedelta(days=2),
                source_updated_at=NOW - timedelta(days=1),
                retrieved_at=NOW - timedelta(days=1),
                content_hash="c" * 64,
            )
        )
        await database.commit()
    await project(factory, world)
    return await resource_for(factory, external_id)


@pytest.mark.asyncio
async def test_stale_vector_after_reindex_is_never_scored(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A rebuilt projection at a new hash must not reuse the old vector."""
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    async with semantic_factory() as database:
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        canonical.content_hash = "z" * 64
        index_row = await database.scalar(
            select(KnowledgeResourceIndex).where(KnowledgeResourceIndex.resource_id == message.id)
        )
        assert index_row is not None
        index_row.source_content_hash = "z" * 64
        await database.commit()
    gateway = FakeEmbeddingGateway(vector=(1.0, 0.0, 0.0))

    response = await run_semantic(semantic_factory, world, gateway)

    assert response.results == ()
    assert SEMANTIC_REASON_OUTDATED in response.coverage.partial_reasons
    async with semantic_factory() as database:
        assert await database.scalar(select(func.count()).select_from(KnowledgeEmbedding)) == 1


@pytest.mark.asyncio
async def test_classification_increase_invalidates_stored_vectors(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A raised classification drops the vector even when the hash is unchanged."""
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    async with semantic_factory() as database:
        row = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.id == message.id)
        )
        assert row is not None
        row.sensitivity = Sensitivity.SENSITIVE.value
        await database.commit()
    gateway = FakeEmbeddingGateway(vector=(1.0, 0.0, 0.0))

    response = await run_semantic(semantic_factory, world, gateway)

    assert response.results == ()
    assert SEMANTIC_REASON_OUTDATED in response.coverage.partial_reasons


@pytest.mark.asyncio
async def test_authority_denial_never_reads_stored_content(
    search_engine: AsyncEngine, semantic_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A denied resource never has titles, chunk text or vectors loaded."""
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    async with semantic_factory() as database:
        for grant in await database.scalars(select(KnowledgeResourcePermission)):
            grant.revoked_at = NOW - timedelta(minutes=1)
        await database.commit()
    statements: list[str] = []

    def record(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    event.listen(search_engine.sync_engine, "before_cursor_execute", record)
    try:
        async with semantic_factory() as database:
            with pytest.raises(SemanticUnavailable):
                await embed_resource(
                    database,
                    workspace_id=world.workspace_id,
                    user_id=world.user_id,
                    resource_id=message.id,
                    request_id=uuid4(),
                    settings=semantic_settings(),
                    gateway=FakeEmbeddingGateway(),
                    now=NOW,
                )
    finally:
        event.remove(search_engine.sync_engine, "before_cursor_execute", record)
    executed = " ".join(statements).lower()
    assert "normalized_text" not in executed
    assert "text_content" not in executed
    assert "knowledge_embeddings" not in executed


@pytest.mark.asyncio
async def test_one_chunk_per_durable_document_request(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A multi-chunk resource buys exactly one attempt per request identifier."""
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    long_message = await seed_long_message(semantic_factory, world)
    async with semantic_factory() as database:
        chunks_total = int(
            await database.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.resource_id == long_message.id)
            )
            or 0
        )
    assert chunks_total >= 2
    gateway = FakeEmbeddingGateway(vector=(0.6, 0.8, 0.0), cost_micros=100)

    async with semantic_factory() as database:
        first = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=long_message.id,
            request_id=uuid4(),
            settings=semantic_settings(),
            chunk_index=0,
            gateway=gateway,
            now=NOW,
        )
    assert first.status == "COMPLETED"
    assert first.chunks_embedded == 1
    assert first.chunks_total == chunks_total
    assert first.reference == SEMANTIC_REASON_PARTIAL
    assert len(gateway.calls) == 1
    async with semantic_factory() as database:
        attempt = await database.scalar(select(KnowledgeEmbeddingRequest))
        assert attempt is not None and attempt.chunk_index == 0 and attempt.cost_micros == 100

    async with semantic_factory() as database:
        second = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=long_message.id,
            request_id=uuid4(),
            settings=semantic_settings(),
            chunk_index=1,
            gateway=gateway,
            now=NOW,
        )
    assert second.status == "COMPLETED" and second.chunk_index == 1
    assert len(gateway.calls) == 2
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 2
        )
        assert await database.scalar(select(func.count()).select_from(KnowledgeEmbedding)) == 2


@pytest.mark.asyncio
async def test_out_of_bounds_chunk_index_is_refused_before_reserving(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    gateway = FakeEmbeddingGateway()
    async with semantic_factory() as database:
        with pytest.raises(SemanticUnavailable):
            await embed_resource(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_id=message.id,
                request_id=uuid4(),
                settings=semantic_settings(),
                chunk_index=MAX_CHUNK_INDEX + 1,
                gateway=gateway,
                now=NOW,
            )
    assert gateway.calls == []
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 0
        )


@pytest.mark.asyncio
async def test_unknown_cost_is_never_reported_as_the_ceiling(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    gateway = FakeEmbeddingGateway(cost_micros=None)
    async with semantic_factory() as database:
        report = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=message.id,
            request_id=uuid4(),
            settings=semantic_settings(),
            gateway=gateway,
            now=NOW,
        )
    assert report.status == "COMPLETED" and report.cost_micros is None
    async with semantic_factory() as database:
        attempt = await database.scalar(select(KnowledgeEmbeddingRequest))
        assert attempt is not None and attempt.cost_micros is None


@pytest.mark.asyncio
async def test_failed_call_keeps_a_known_cost_and_unknown_stays_unknown(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    # An invalid vector after a priced call keeps the observed cost, not the ceiling.
    gateway = FakeEmbeddingGateway(vector=(0.0, 0.0, 0.0), cost_micros=777)
    async with semantic_factory() as database:
        with pytest.raises(SemanticUnavailable):
            await embed_resource(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_id=message.id,
                request_id=uuid4(),
                settings=semantic_settings(),
                gateway=gateway,
                now=NOW,
            )
    async with semantic_factory() as database:
        attempt = await database.scalar(select(KnowledgeEmbeddingRequest))
        assert attempt is not None and attempt.status == "FAILED"
        assert attempt.cost_micros == 777
        assert attempt.cost_micros != 50_000


@pytest.mark.asyncio
async def test_flags_off_block_paid_document_work(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    gateway = FakeEmbeddingGateway()
    for settings in (
        semantic_settings(knowledge_semantic_enabled=False),
        semantic_settings(knowledge_enabled=False),
    ):
        async with semantic_factory() as database:
            with pytest.raises(SemanticDisabled):
                await embed_resource(
                    database,
                    workspace_id=world.workspace_id,
                    user_id=world.user_id,
                    resource_id=message.id,
                    request_id=uuid4(),
                    settings=settings,
                    gateway=gateway,
                    now=NOW,
                )
    assert gateway.calls == []
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 0
        )


@pytest.mark.asyncio
async def test_missing_vectors_on_a_second_resource_report_partial_coverage(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    await stored_vector(semantic_factory, resource=message)
    gateway = FakeEmbeddingGateway(vector=(1.0, 0.0, 0.0))

    response = await run_semantic(semantic_factory, world, gateway)

    assert [item.resource_id for item in response.results] == [message.id]
    assert SEMANTIC_REASON_MISSING in response.coverage.partial_reasons


@pytest.mark.asyncio
async def test_corrupt_stored_vector_yields_incomplete_coverage(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A corrupt persisted row is skipped with honest partial coverage, never an error."""
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    index_hash = await index_hash_for(semantic_factory, message.id)
    async with semantic_factory() as database:
        database.add(
            KnowledgeEmbedding(
                workspace_id=world.workspace_id,
                resource_id=message.id,
                chunk_index=0,
                source_content_hash=index_hash,
                sensitivity=message.sensitivity,
                provider=PROVIDER,
                model=MODEL,
                registry_revision=REVISION,
                embedding_version=EMBEDDING_VERSION,
                dimension=3,
                vector=["0.1", 0.2, 0.3],
                generated_at=NOW,
            )
        )
        await database.commit()
    gateway = FakeEmbeddingGateway(vector=(1.0, 0.0, 0.0))

    response = await run_semantic(semantic_factory, world, gateway)

    assert response.results == ()
    assert SEMANTIC_REASON_PARTIAL in response.coverage.partial_reasons
    assert SEMANTIC_REASON_MISSING in response.coverage.partial_reasons


def test_extreme_finite_vectors_normalize_and_corrupt_rows_are_rejected() -> None:
    unit = normalize_vector((1e308, 1e308))
    assert unit[0] == pytest.approx(2**-0.5) and unit[1] == pytest.approx(2**-0.5)
    tiny = normalize_vector((1e-320, 0.0))
    assert tiny == (1.0, 0.0)
    with pytest.raises(ValueError):
        normalize_vector((0.0, 0.0))
    with pytest.raises(ValueError):
        normalize_vector((float("inf"), 0.0))
    assert parse_stored_vector(["0.1", 0.2, 0.3], 3) is None
    assert parse_stored_vector([True, 0.2, 0.3], 3) is None
    assert parse_stored_vector([float("nan"), 0.2, 0.3], 3) is None
    assert parse_stored_vector([0.1, 0.2], 3) is None
    assert parse_stored_vector([0.1, 0.2, 0.3], 3) == (0.1, 0.2, 0.3)


@pytest.mark.asyncio
async def test_post_call_fence_uses_current_time_not_the_request_start(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A VIEW grant that expires mid-call cannot publish its vector."""
    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    async with semantic_factory() as database:
        for grant in await database.scalars(select(KnowledgeResourcePermission)):
            grant.valid_until = NOW + timedelta(minutes=60)
        await database.commit()
    gateway = FakeEmbeddingGateway()

    async with semantic_factory() as database:
        report = await embed_resource(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=message.id,
            request_id=uuid4(),
            settings=semantic_settings(),
            gateway=gateway,
            now=NOW,
            clock=lambda: NOW + timedelta(minutes=90),
        )

    assert report.status == "DISCARDED"
    async with semantic_factory() as database:
        assert await database.scalar(select(func.count()).select_from(KnowledgeEmbedding)) == 0
        attempt = await database.scalar(select(KnowledgeEmbeddingRequest))
        assert attempt is not None and attempt.status == "DISCARDED"


@pytest.mark.asyncio
async def test_context_rejects_altered_text_and_wrong_task_shape(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    from navox.ai.foundation.contracts import ProviderPolicy

    world = await load_world(semantic_factory)
    await project(semantic_factory, world)
    message = await resource_for(semantic_factory, "message-1")
    binding = EmbeddingBinding(
        resource_id=message.id,
        source_content_hash=await index_hash_for(semantic_factory, message.id),
        source_connection_id=message.source_connection_id,
        source_resource_id=message.source_resource_id,
        chunk_index=0,
    )
    policy = ProviderPolicy(
        workspace_id=world.workspace_id,
        user_id=world.user_id,
        revision=1,
        grants=(),
        allow_fallback=False,
        max_fallbacks=0,
    )
    settings = semantic_settings()

    def context_for(request: EmbeddingRequest) -> KnowledgeContext:
        return KnowledgeContext(semantic_factory, settings=settings, request=request)

    tampered = EmbeddingRequest(
        workspace_id=world.workspace_id,
        user_id=world.user_id,
        text="tampered text that is not the stored chunk",
        purpose="RETRIEVAL_DOCUMENT",
        sensitivity=Sensitivity.PERSONAL,
        binding=binding,
    )
    with pytest.raises(ContextDenied):
        await context_for(tampered).build(_embedding_task(tampered, policy), {})

    honest = EmbeddingRequest(
        workspace_id=world.workspace_id,
        user_id=world.user_id,
        text=await stored_chunk_text(semantic_factory, message.id, 0),
        purpose="RETRIEVAL_DOCUMENT",
        sensitivity=Sensitivity.PERSONAL,
        binding=binding,
    )
    downgraded = _embedding_task(honest, policy).model_copy(
        update={"sensitivity": Sensitivity.SENSITIVE}
    )
    with pytest.raises(ContextDenied):
        await context_for(honest).build(downgraded, {})

    query_with_binding = EmbeddingRequest(
        workspace_id=world.workspace_id,
        user_id=world.user_id,
        text="quarterly budget",
        purpose="RETRIEVAL_QUERY",
        sensitivity=Sensitivity.PERSONAL,
        binding=binding,
    )
    with pytest.raises(ContextDenied):
        await context_for(query_with_binding).build(_embedding_task(query_with_binding, policy), {})

    document_without_binding = EmbeddingRequest(
        workspace_id=world.workspace_id,
        user_id=world.user_id,
        text="quarterly budget",
        purpose="RETRIEVAL_DOCUMENT",
        sensitivity=Sensitivity.PERSONAL,
    )
    with pytest.raises(ContextDenied):
        await context_for(document_without_binding).build(
            _embedding_task(document_without_binding, policy), {}
        )

    flags_off = KnowledgeContext(
        semantic_factory,
        settings=semantic_settings(knowledge_semantic_enabled=False),
        request=honest,
    )
    with pytest.raises(ContextDenied):
        await flags_off.build(_embedding_task(honest, policy), {})

    refused = FakeEmbeddingGateway()
    async with semantic_factory() as database:
        with pytest.raises(SemanticUnavailable):
            await embed_resource(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_id=message.id,
                request_id=uuid4(),
                settings=semantic_settings(),
                gateway=refused,
                now=NOW,
                chunk_index=5,
            )
    assert refused.calls == []


async def stored_chunk_text(
    factory: async_sessionmaker[AsyncSession], resource_id: UUID, chunk_index: int
) -> str:
    async with factory() as database:
        text = await database.scalar(
            select(KnowledgeChunk.text_content).where(
                KnowledgeChunk.resource_id == resource_id,
                KnowledgeChunk.chunk_index == chunk_index,
            )
        )
        assert text
        return text


PG_DSN = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "")


@pytest.mark.skipif(
    not PG_DSN.startswith("postgresql"),
    reason="Hourly quota serialization needs a real row lock (PostgreSQL)",
)
@pytest.mark.asyncio
async def test_postgres_quota_race_allows_only_one_reservation(
    semantic_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two concurrent distinct identifiers near the quota: exactly one wins."""
    world = await load_world(semantic_factory)

    async def reserve(request_id: UUID) -> str:
        async with semantic_factory() as database:
            try:
                _row, created = await reserve_attempt(
                    database,
                    workspace_id=world.workspace_id,
                    user_id=world.user_id,
                    request_id=request_id,
                    scope="QUERY",
                    quota=1,
                    now=NOW,
                )
            except SemanticQuotaExceeded:
                return "quota"
            return "created" if created else "replay"

    results = await asyncio.gather(reserve(uuid4()), reserve(uuid4()))
    assert sorted(results) == ["created", "quota"]
    async with semantic_factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(KnowledgeEmbeddingRequest)) == 1
        )


def test_maximum_finite_vector_dimensions_and_oversized_integer() -> None:
    from navox.knowledge.retrieval import cosine_similarity

    assert normalize_vector((1e308,) * 4096)[0] == pytest.approx(1 / 64)
    assert cosine_similarity((1e308, 1e308), (1e308, 1e308)) == pytest.approx(1)
    assert parse_stored_vector([10**1000], 1) is None
    with pytest.raises(ValueError):
        normalize_vector((10**1000,))
