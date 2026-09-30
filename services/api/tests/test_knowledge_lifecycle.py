"""Lifecycle flows use canonical authority; stale events never recreate content."""

from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from navox.db.knowledge import (
    KnowledgeChunk,
    KnowledgeEmbedding,
    KnowledgeEntity,
    KnowledgeResource,
)
from navox.db.models import ConnectorConnection, ConnectorResource
from navox.knowledge import activities
from navox.knowledge.jobs import KnowledgeResourceWork
from navox.knowledge.lifecycle import cleanup_orphan_entities, process_resource_work
from tests.knowledge_search_support import NOW
from tests.test_knowledge_api import knowledge_api as knowledge_api
from tests.test_knowledge_graph import seed_graph


async def setup(fixture):
    child, parent, entity, child_source, parent_source, connection = await seed_graph(fixture)
    async with fixture["factory"]() as database:
        resource = await database.get(KnowledgeResource, child)
        database.add(
            KnowledgeEmbedding(
                workspace_id=fixture["workspace_id"],
                resource_id=child,
                chunk_index=0,
                source_content_hash="a" * 64,
                sensitivity=resource.sensitivity,
                provider="synthetic",
                model="test",
                registry_revision="test",
                embedding_version="v1",
                dimension=2,
                vector=[1.0, 0.0],
                generated_at=NOW,
            )
        )
        await database.commit()
    payload = KnowledgeResourceWork(
        str(fixture["workspace_id"]), str(fixture["user_id"]), str(child_source), "a" * 64
    )
    return child, parent, entity, child_source, parent_source, connection, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["deleted", "disconnected", "revoked"])
async def test_cleanup_even_disabled_removes_text_vectors_and_graph(knowledge_api, change):
    child, parent, entity, source, _, connection, payload = await setup(knowledge_api)
    async with knowledge_api["factory"]() as database:
        if change == "deleted":
            (await database.get(ConnectorResource, source)).deleted = True
        elif change == "disconnected":
            (await database.get(ConnectorConnection, connection)).status = "DISCONNECTED"
        else:
            (await database.get(ConnectorConnection, connection)).authorized_capabilities = []
        await database.commit()
        result = await process_resource_work(
            database, payload=payload, settings=knowledge_api["disabled"], now=NOW
        )
        assert result == "CLEANED"
        await database.commit()
    async with knowledge_api["factory"]() as database:
        resource = await database.get(KnowledgeResource, child)
        assert resource.normalized_text is None and resource.title is None
        assert (resource.deleted_at is not None) == (change != "revoked")
        for model in (KnowledgeChunk, KnowledgeEmbedding):
            assert await database.scalar(select(model.id).where(model.resource_id == child)) is None
        assert await database.get(KnowledgeEntity, entity) is None
        # Cleaning one resource does not erase its parent or upstream canonical row.
        assert await database.get(KnowledgeResource, parent) is not None
        assert await database.get(ConnectorResource, source) is not None


@pytest.mark.asyncio
async def test_stale_update_and_delete_event_cannot_overwrite_or_erase_current_source(
    knowledge_api,
):
    child, _, _, source, _, _, payload = await setup(knowledge_api)
    async with knowledge_api["factory"]() as database:
        canonical = await database.get(ConnectorResource, source)
        canonical.content_hash = "b" * 64
        canonical.canonical = dict(canonical.canonical, subject="New current revision")
        await database.commit()
        assert (
            await process_resource_work(
                database, payload=payload, settings=knowledge_api["enabled"], now=NOW
            )
            == "STALE_EVENT"
        )
        current = replace(payload, expected_content_hash="b" * 64)
        assert (
            await process_resource_work(
                database, payload=current, settings=knowledge_api["enabled"], now=NOW
            )
            == "INDEXED"
        )
        assert (
            await process_resource_work(
                database,
                payload=payload,
                settings=knowledge_api["enabled"],
                delete_only=True,
                now=NOW,
            )
            == "NOT_DELETED"
        )
        await database.commit()
    async with knowledge_api["factory"]() as database:
        assert (await database.get(KnowledgeResource, child)).title == "New current revision"
        assert (
            await database.scalar(
                select(KnowledgeEmbedding.id).where(KnowledgeEmbedding.resource_id == child)
            )
            is None
        )


@pytest.mark.asyncio
async def test_retired_projection_cannot_be_resurrected_even_by_force(knowledge_api):
    child, _, _, source, _, _, payload = await setup(knowledge_api)
    async with knowledge_api["factory"]() as database:
        canonical = await database.get(ConnectorResource, source)
        canonical.deleted = True
        await database.commit()
        assert (
            await process_resource_work(
                database, payload=payload, settings=knowledge_api["enabled"], now=NOW
            )
            == "CLEANED"
        )
        await database.commit()
        canonical.deleted = False
        await database.commit()
        assert (
            await process_resource_work(
                database, payload=payload, settings=knowledge_api["enabled"], force=True, now=NOW
            )
            == "UNAVAILABLE"
        )
        await database.commit()
        assert (await database.get(KnowledgeResource, child)).normalized_text is None


@pytest.mark.asyncio
async def test_foreign_scope_work_is_non_authoritative_and_orphan_cleanup(knowledge_api):
    child, _, entity, source, _, _, payload = await setup(knowledge_api)
    async with knowledge_api["factory"]() as database:
        assert (
            await process_resource_work(
                database,
                payload=replace(payload, user_id=str(uuid4())),
                settings=knowledge_api["enabled"],
                now=NOW,
            )
            == "UNAVAILABLE"
        )
        assert (
            await process_resource_work(
                database,
                payload=replace(payload, workspace_id=str(uuid4())),
                settings=knowledge_api["enabled"],
                now=NOW,
            )
            == "ABSENT"
        )
        assert (await database.get(KnowledgeResource, child)).normalized_text
        await database.execute(delete(ConnectorResource).where(ConnectorResource.id == source))
        await database.flush()
        assert await cleanup_orphan_entities(database) == 1
        await database.commit()
        assert await database.get(KnowledgeEntity, entity) is None


@pytest.mark.asyncio
async def test_actual_activities_project_and_page_identifiers_only(knowledge_api, monkeypatch):
    child, _, _, source, _, _, payload = await setup(knowledge_api)
    monkeypatch.setattr(activities, "get_session_factory", lambda: knowledge_api["factory"])
    monkeypatch.setattr(activities, "get_settings", lambda: knowledge_api["enabled"])
    page = await activities.knowledge_source_page_activity(None)
    assert len(page.resources) == 2
    assert all(
        set(asdict(item))
        == {"workspace_id", "user_id", "source_resource_id", "expected_content_hash"}
        for item in page.resources
    )
    assert await activities.knowledge_resource_activity(payload) == "INDEXED"
    assert await activities.knowledge_reindex_activity(payload) == "INDEXED"
    assert await activities.knowledge_delete_activity(payload) == "NOT_DELETED"


@pytest.mark.asyncio
async def test_all_knowledge_workflows_load_in_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from navox.workflows import knowledge

    runner = SandboxedWorkflowRunner()
    classes = [
        getattr(knowledge, name)
        for name in dir(knowledge)
        if name.startswith("Knowledge") and name.endswith("Workflow")
    ]
    assert len(classes) == 8
    for cls in classes:
        runner.prepare_workflow(workflow._Definition.must_from_class(cls))


@pytest.mark.asyncio
async def test_real_connector_sync_projects_retained_resource_after_acceptance(knowledge_api):
    from navox.connectors.contracts import CanonicalResource, SyncPage, stable_resource_id
    from navox.connectors.registry import ConnectorRegistry
    from navox.connectors.runtime import ConnectorRuntime
    from tests.knowledge_search_support import READ_CAPABILITY, manifest
    from tests.test_connector_runtime import FixtureConnector

    _, _, _, _, _, connection, _ = await setup(knowledge_api)

    class ConnectedFixture(FixtureConnector):
        def get_manifest(self):
            return manifest("google-gmail", READ_CAPABILITY, ["communication.message"])

        async def sync(self, request):
            return SyncPage(
                resources=[
                    CanonicalResource(
                        resource_id=stable_resource_id(
                            request.connection_id, "communication.message", "new-message"
                        ),
                        workspace_id=request.workspace_id,
                        connector_connection_id=request.connection_id,
                        provider="fixture",
                        resource_type="communication.message",
                        external_id="new-message",
                        canonical={
                            "subject": "New sync result",
                            "content": "Retained and indexed after acceptance",
                            "source_type": "communication.message",
                        },
                        retrieved_at=NOW,
                    )
                ],
                next_cursor="done",
                has_more=False,
            )

    registry = ConnectorRegistry()
    registry.register(ConnectedFixture({}).get_manifest(), ConnectedFixture)
    runtime = ConnectorRuntime(registry, knowledge_settings=knowledge_api["enabled"])

    async def consume(resource):
        return []

    async with knowledge_api["factory"]() as database:
        run = await runtime.sync(
            database,
            connection_id=connection,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request_id=uuid4(),
            policy_allowed={READ_CAPABILITY},
            consume=consume,
        )
        assert run.status == "completed"
        projected = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.external_resource_id == "new-message")
        )
        assert projected is not None
        assert projected.title == "New sync result"
        assert "Retained and indexed" in projected.normalized_text


@pytest.mark.asyncio
async def test_embedding_jobs_are_flagged_bounded_and_durable(knowledge_api, monkeypatch):
    from navox.knowledge.embeddings import reserve_attempt
    from navox.knowledge.jobs import KnowledgeEmbeddingWork

    child, _, _, _, _, _, payload = await setup(knowledge_api)
    monkeypatch.setattr(activities, "get_session_factory", lambda: knowledge_api["factory"])
    settings = knowledge_api["enabled"].model_copy(update={"knowledge_semantic_enabled": False})
    monkeypatch.setattr(activities, "get_settings", lambda: settings)
    assert await activities.knowledge_pending_embeddings_activity(payload) == []
    settings.knowledge_semantic_enabled = True
    jobs = await activities.knowledge_pending_embeddings_activity(payload)
    assert len(jobs) == 1
    assert jobs == await activities.knowledge_pending_embeddings_activity(payload)
    job = jobs[0]
    assert isinstance(job, KnowledgeEmbeddingWork)
    assert "text" not in asdict(job)
    async with knowledge_api["factory"]() as database:
        from uuid import UUID

        await reserve_attempt(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request_id=UUID(job.request_id),
            scope="DOCUMENT",
            quota=20,
            resource_id=child,
            chunk_index=0,
        )
    assert await activities.knowledge_pending_embeddings_activity(payload) == []
    # Default off refuses even an already constructed payload before provider work.
    settings.knowledge_semantic_enabled = False
    assert await activities.knowledge_embedding_activity(job) == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_refresh_uses_existing_dispatch_and_honest_pending_status(knowledge_api, monkeypatch):
    from navox.api import knowledge_lifecycle

    child, _, _, _, _, connection, _ = await setup(knowledge_api)
    calls = []

    async def dispatch(target, payload, request, account, database, settings):
        calls.append((target, payload))
        return {"dispatch_status": "queued"}

    monkeypatch.setattr(knowledge_lifecycle, "sync_connection", dispatch)
    url = f"/api/v1/knowledge/resources/{child}/refresh"
    command = {"request_id": str(uuid4())}
    response = await knowledge_api["client"].post(
        url, json=command, headers=knowledge_api["origin"]
    )
    assert response.status_code == 202
    assert response.json()["state"] == "REFRESHING"
    assert calls[0][0] == connection and calls[0][1].source == "resources"

    async def pending(*args):
        return {"dispatch_status": "pending"}

    monkeypatch.setattr(knowledge_lifecycle, "sync_connection", pending)
    response = await knowledge_api["client"].post(
        url, json=command, headers=knowledge_api["origin"]
    )
    assert response.json()["state"] == "ERROR"
    assert (
        await knowledge_api["client"].post(
            url, json=command, headers={"Origin": "https://evil.example"}
        )
    ).status_code == 403
    assert (
        await knowledge_api["client"].post(
            url, json={**command, "url": "https://evil.example"}, headers=knowledge_api["origin"]
        )
    ).status_code == 422
    async with knowledge_api["factory"]() as database:
        (await database.get(ConnectorConnection, connection)).authorized_capabilities = []
        await database.commit()
    assert (
        await knowledge_api["client"].post(url, json=command, headers=knowledge_api["origin"])
    ).status_code == 404
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_reindex_does_not_extend_source_freshness(knowledge_api):
    from datetime import timedelta

    from navox.knowledge.contracts import stored_utc
    from navox.knowledge.freshness import fresh_until

    child, _, _, source, _, _, payload = await setup(knowledge_api)
    async with knowledge_api["factory"]() as database:
        original = stored_utc((await database.get(ConnectorResource, source)).retrieved_at)
        assert (
            await process_resource_work(
                database,
                payload=payload,
                settings=knowledge_api["enabled"],
                force=True,
                now=NOW + timedelta(days=7),
            )
            == "INDEXED"
        )
        await database.commit()
        resource = await database.get(KnowledgeResource, child)
        assert stored_utc(resource.fresh_until) == original + timedelta(minutes=15)
        assert stored_utc(resource.fresh_until) < NOW
    assert fresh_until("unknown.read", NOW) is None
