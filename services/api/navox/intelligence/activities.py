from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy import or_, select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from navox.agent.audit import add_audit_event
from navox.ai.factory import build_ai_gateway
from navox.core.settings import get_settings
from navox.db.models import (
    Commitment,
    Connection,
    IncomingEvent,
    IntelligenceCursor,
    User,
    WorkspaceMembership,
)
from navox.db.session import get_session_factory
from navox.intelligence.extraction import OperationalExtractor
from navox.intelligence.jobs import SourceWork, WorkspaceWork


@activity.defn
async def process_source_activity(payload: SourceWork) -> int:
    from navox.intelligence.attention import evaluate_workspace_attention
    from navox.intelligence.ingestion import process_connection

    started = monotonic()
    settings = get_settings()
    async with get_session_factory()() as database:
        connection = await database.scalar(
            select(Connection).where(
                Connection.id == UUID(payload.connection_id),
                Connection.user_id == UUID(payload.user_id),
                Connection.workspace_id == UUID(payload.workspace_id),
                Connection.status == "active",
            )
        )
        user = await database.get(User, UUID(payload.user_id))
        if connection is None or user is None:
            return 0
        if user.agent_paused or settings.ai_provider == "disabled":
            return 0
        event = None
        if payload.event_id:
            event = await database.scalar(
                select(IncomingEvent).where(
                    IncomingEvent.id == UUID(payload.event_id),
                    IncomingEvent.connection_id == connection.id,
                    IncomingEvent.user_id == connection.user_id,
                    IncomingEvent.workspace_id == connection.workspace_id,
                    IncomingEvent.source == payload.source,
                )
            )
            if event is None or event.intelligence_status == "completed":
                return 0
        try:
            ids = await process_connection(
                database,
                connection_id=connection.id,
                source=payload.source,
                settings=settings,
                extractor=OperationalExtractor(build_ai_gateway(settings)),
            )
            await evaluate_workspace_attention(
                database, user_id=user.id, workspace_id=connection.workspace_id
            )
            if event:
                event.intelligence_status = "completed"
                event.processed_at = datetime.now(UTC)
            add_audit_event(
                database,
                user_id=user.id,
                workspace_id=connection.workspace_id,
                event_type="intelligence.source.processed",
                entity_type="connection",
                entity_id=connection.id,
                metadata={
                    "source": payload.source,
                    "commitment_count": len(ids),
                    "duration_ms": round((monotonic() - started) * 1000),
                },
            )
            await database.commit()
            return len(ids)
        except Exception as error:
            # No source text, credential or provider response enters logs or workflow history.
            await database.rollback()
            if payload.event_id:
                failed_event = await database.get(IncomingEvent, UUID(payload.event_id))
                if failed_event is not None:
                    failed_event.intelligence_status = "failed"
                    failed_event.processed_at = datetime.now(UTC)
            add_audit_event(
                database,
                user_id=UUID(payload.user_id),
                workspace_id=UUID(payload.workspace_id),
                event_type="intelligence.source.failed",
                entity_type="connection",
                entity_id=UUID(payload.connection_id),
                metadata={
                    "source": payload.source,
                    "error_type": type(error).__name__,
                    "duration_ms": round((monotonic() - started) * 1000),
                },
            )
            await database.commit()
            raise ApplicationError(
                "Source processing failed; cursor retained for retry",
                type="IntelligenceProcessingFailure",
            ) from None


@activity.defn
async def refresh_intelligence_activity(payload: WorkspaceWork) -> int:
    from navox.intelligence.attention import evaluate_workspace_attention
    from navox.intelligence.state import reevaluate_commitment

    async with get_session_factory()() as database:
        membership = await database.get(
            WorkspaceMembership, (UUID(payload.workspace_id), UUID(payload.user_id))
        )
        user = await database.get(User, UUID(payload.user_id))
        if membership is None or user is None or user.agent_paused:
            return 0
        query = (
            select(Commitment)
            .where(
                Commitment.user_id == user.id, Commitment.workspace_id == UUID(payload.workspace_id)
            )
            .with_for_update()
        )
        if payload.commitment_id:
            query = query.where(Commitment.id == UUID(payload.commitment_id))
        commitments = list(await database.scalars(query))
        for commitment in commitments:
            if commitment.intelligence_metadata:
                await reevaluate_commitment(database, commitment=commitment, now=datetime.now(UTC))
        results = await evaluate_workspace_attention(
            database, user_id=user.id, workspace_id=UUID(payload.workspace_id)
        )
        await database.commit()
        return len(results)


@activity.defn
async def intelligence_workspaces_activity() -> list[WorkspaceWork]:
    async with get_session_factory()() as database:
        rows = (
            await database.execute(
                select(WorkspaceMembership.user_id, WorkspaceMembership.workspace_id)
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(User.agent_paused.is_(False))
            )
        ).all()
        return [WorkspaceWork(str(user_id), str(workspace_id)) for user_id, workspace_id in rows]


@activity.defn
async def pending_intelligence_activity() -> list[SourceWork]:
    """Recover webhook dispatch gaps and repair missed notifications with delta reads."""
    from navox.intelligence.ingestion import renew_source_watch

    settings = get_settings()
    if settings.ai_provider == "disabled":
        return []
    now = datetime.now(UTC)
    pending: list[SourceWork] = []
    async with get_session_factory()() as database:
        events = list(
            await database.scalars(
                select(IncomingEvent)
                .join(Connection, IncomingEvent.connection_id == Connection.id)
                .join(User, Connection.user_id == User.id)
                .where(
                    Connection.status == "active",
                    User.agent_paused.is_(False),
                    IncomingEvent.source.in_(["gmail", "calendar"]),
                    or_(
                        IncomingEvent.intelligence_status == "pending",
                        (IncomingEvent.intelligence_status == "failed")
                        & (IncomingEvent.processed_at < now - timedelta(minutes=10)),
                    ),
                )
                .order_by(IncomingEvent.received_at)
                .limit(50)
            )
        )
        seen: set[tuple[UUID, str]] = set()
        for event in events:
            pending.append(
                SourceWork(
                    str(event.connection_id),
                    str(event.user_id),
                    str(event.workspace_id),
                    event.source,
                    str(event.id),
                )
            )
            seen.add((event.connection_id, event.source))
        connections = list(
            await database.scalars(
                select(Connection)
                .join(User, Connection.user_id == User.id)
                .where(
                    Connection.status == "active",
                    Connection.provider == "google",
                    User.agent_paused.is_(False),
                )
            )
        )
        for connection in connections:
            for source, scope in (
                ("gmail", "https://www.googleapis.com/auth/gmail.readonly"),
                ("calendar", "https://www.googleapis.com/auth/calendar.events.readonly"),
            ):
                if scope not in connection.granted_scopes:
                    continue
                cursor = await database.scalar(
                    select(IntelligenceCursor).where(
                        IntelligenceCursor.connection_id == connection.id,
                        IntelligenceCursor.source == source,
                    )
                )
                last = cursor.updated_at if cursor else None
                if last and last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if (connection.id, source) not in seen and (
                    last is None or last < now - timedelta(minutes=30)
                ):
                    pending.append(
                        SourceWork(
                            str(connection.id),
                            str(connection.user_id),
                            str(connection.workspace_id),
                            source,
                        )
                    )
                try:
                    async with get_session_factory()() as watch_database:
                        await renew_source_watch(
                            watch_database,
                            connection_id=connection.id,
                            source=source,
                            settings=settings,
                        )
                        await watch_database.commit()
                except Exception:
                    # Delta reconciliation remains available while notification setup is offline.
                    pass
                if len(pending) >= 100:
                    return pending
    return pending
