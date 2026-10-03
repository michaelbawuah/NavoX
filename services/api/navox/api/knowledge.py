"""Authenticated connected search, evidence and resource detail (SPEC-007).

Search never performs an external action, never generates an answer and never
accepts workspace, user, permission or exclusion authority from the client.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.api.knowledge_ask import router as knowledge_ask_router
from navox.connectors.authorization import ConnectorAccessDenied
from navox.knowledge.class_schedule import live_class_navigation_target, live_class_source_snapshot
from navox.knowledge.embeddings import (
    EmbeddingGateway,
    SemanticDisabled,
    SemanticQuotaExceeded,
    SemanticRejected,
    SemanticRequestReplay,
    SemanticUnavailable,
    semantic_search,
)
from navox.knowledge.exclusions import (
    create_exclusion,
    delete_exclusion,
    exclusion_view,
    list_exclusions,
)
from navox.knowledge.recent import clear_recent_searches, list_recent_searches
from navox.knowledge.search_contracts import (
    MAX_QUERY_LENGTH,
    MAX_RESULT_LIMIT,
    DateRange,
    ExclusionCreate,
    ExclusionView,
    RecentSearchView,
    ResourceDetail,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SemanticSearchRequest,
)
from navox.knowledge.service import KnowledgeUnavailable, load_resource_detail, search_knowledge

router = APIRouter(tags=["knowledge"])
router.include_router(knowledge_ask_router)


@router.get("/knowledge/class-sources")
async def class_sources(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    """Permission-fenced Canvas/Calendar projection for SPEC-008 next class."""
    knowledge_enabled(settings.knowledge_enabled)
    reader = getattr(request.app.state, "class_source_reader", live_class_source_snapshot)
    return await reader(
        database, workspace_id=account.workspace.id, user_id=account.user.id, settings=settings
    )


@router.get("/knowledge/class-navigation")
async def class_navigation(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    connection_id: UUID,
    resource_id: UUID,
) -> dict[str, str]:
    """Resolve an upcoming class event only after a fresh scoped provider read."""
    knowledge_enabled(settings.knowledge_enabled)
    try:
        url = await live_class_navigation_target(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            settings=settings,
            connection_id=connection_id,
            resource_id=resource_id,
        )
    except ConnectorAccessDenied:
        url = None
    if url is None:
        raise HTTPException(404, "Class navigation is unavailable")
    return {"url": url}


def knowledge_enabled(enabled: bool) -> None:
    """The knowledge feature is default off; enabling it in tests is not deployment."""
    if not enabled:
        raise HTTPException(404, "Connected search is not enabled")


def _unavailable(error: KnowledgeUnavailable) -> HTTPException:
    return HTTPException(403, str(error) or "Connected search is unavailable")


def semantic_enabled(enabled: bool) -> None:
    """Paid semantic search is a separate opt-in from ordinary search."""
    if not enabled:
        raise HTTPException(404, "Semantic search is not enabled")


def injected_gateway(request: Request) -> EmbeddingGateway | None:
    """Test-only seam. Production builds the registered gateway from settings."""
    return getattr(request.app.state, "knowledge_embedding_gateway", None)


@router.get("/search", response_model=SearchResponse)
async def search(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_LENGTH)],
    mode: Annotated[SearchMode, Query()] = SearchMode.AUTO,
    sources: Annotated[list[UUID], Query()] = [],  # noqa: B006 - FastAPI query list
    types: Annotated[list[str], Query()] = [],  # noqa: B006 - FastAPI query list
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULT_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> SearchResponse:
    knowledge_enabled(settings.knowledge_enabled)
    try:
        payload = SearchRequest(
            query=q,
            mode=mode,
            sources=tuple(sources),
            types=tuple(types),  # type: ignore[arg-type]
            date_range=(
                DateRange(start=start, end=end) if start is not None or end is not None else None
            ),
            limit=limit,
            offset=offset,
        )
    except ValidationError as error:
        raise HTTPException(422, "Invalid search request") from error
    try:
        return await search_knowledge(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            request=payload,
            settings=settings,
        )
    except KnowledgeUnavailable as error:
        raise _unavailable(error) from None


@router.post("/search/query", response_model=SearchResponse)
async def search_query(
    request: Request,
    payload: SearchRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    remember: bool = True,
) -> SearchResponse:
    require_origin(request, settings.web_origin)
    knowledge_enabled(settings.knowledge_enabled)
    try:
        return await search_knowledge(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            request=payload,
            settings=settings,
            record_recent=remember,
        )
    except KnowledgeUnavailable as error:
        raise _unavailable(error) from None


@router.post("/search/semantic", response_model=SearchResponse)
async def search_semantic(
    request: Request,
    payload: SemanticSearchRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SearchResponse:
    """Explicit paid semantic search; GET search never buys a provider request."""
    require_origin(request, settings.web_origin)
    knowledge_enabled(settings.knowledge_enabled)
    semantic_enabled(settings.knowledge_semantic_enabled)
    try:
        return await semantic_search(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            request=payload,
            settings=settings,
            gateway=injected_gateway(request),
        )
    except SemanticRequestReplay as error:
        raise HTTPException(409, "This request identifier was already used") from error
    except SemanticQuotaExceeded as error:
        raise HTTPException(429, str(error)) from error
    except SemanticRejected as error:
        raise HTTPException(422, str(error)) from error
    except SemanticDisabled as error:
        # Defensive: the flag is checked above, before any work is scheduled.
        raise HTTPException(404, str(error)) from error
    except SemanticUnavailable as error:
        # The account or its membership changed before the reservation landed.
        raise HTTPException(403, str(error) or "Semantic search is unavailable") from error
    except KnowledgeUnavailable as error:
        raise _unavailable(error) from None


@router.get("/search/recent", response_model=list[RecentSearchView])
async def recent_searches(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[RecentSearchView]:
    knowledge_enabled(settings.knowledge_enabled)
    return await list_recent_searches(
        database, workspace_id=account.workspace.id, user_id=account.user.id
    )


@router.delete("/search/recent")
async def clear_recent(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, int]:
    require_origin(request, settings.web_origin)
    knowledge_enabled(settings.knowledge_enabled)
    removed = await clear_recent_searches(
        database, workspace_id=account.workspace.id, user_id=account.user.id
    )
    await database.commit()
    return {"cleared": removed}


@router.get("/search/exclusions", response_model=list[ExclusionView])
async def exclusions(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[ExclusionView]:
    knowledge_enabled(settings.knowledge_enabled)
    rows = await list_exclusions(
        database, workspace_id=account.workspace.id, user_id=account.user.id
    )
    return [exclusion_view(row) for row in rows]


@router.post("/search/exclusions", response_model=ExclusionView, status_code=201)
async def add_exclusion(
    request: Request,
    payload: ExclusionCreate,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ExclusionView:
    require_origin(request, settings.web_origin)
    knowledge_enabled(settings.knowledge_enabled)
    try:
        row = await create_exclusion(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            payload=payload,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except IntegrityError:
        # Never echo database detail from a rejected write.
        raise HTTPException(422, "Unknown or unsupported exclusion target") from None
    await database.commit()
    return exclusion_view(row)


@router.delete("/search/exclusions/{exclusion_id}")
async def remove_exclusion(
    request: Request,
    exclusion_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, bool]:
    require_origin(request, settings.web_origin)
    knowledge_enabled(settings.knowledge_enabled)
    removed = await delete_exclusion(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        exclusion_id=exclusion_id,
    )
    if not removed:
        raise HTTPException(404, "Exclusion not found")
    await database.commit()
    return {"removed": True}


@router.get("/knowledge/resources/{resource_id}", response_model=ResourceDetail)
async def resource_detail(
    resource_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ResourceDetail:
    knowledge_enabled(settings.knowledge_enabled)
    try:
        detail = await load_resource_detail(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            resource_id=resource_id,
        )
    except KnowledgeUnavailable as error:
        raise _unavailable(error) from None
    if detail is None:
        # A denied resource is indistinguishable from an unknown one.
        raise HTTPException(404, "Resource not found")
    return detail
