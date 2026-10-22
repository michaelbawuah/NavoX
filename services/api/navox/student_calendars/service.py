"""Fresh native authority for the separate calendar-availability purpose.

No provider request or event write occurs here. Reset retains a generation fence;
ordinary Google/Gmail connections and their credentials are not disconnected.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.core.credential_vault import CredentialVault, CredentialVaultError
from navox.core.settings import Settings
from navox.db.models import Connection, ConnectorConnection, OAuthAuthorizationAttempt, UserSession
from navox.db.student_calendars import (
    StudentCalendarEvent,
    StudentCalendarGrant,
    StudentCalendarState,
    StudentCalendarWriteGrant,
)
from navox.student import service as schedule
from navox.student_calendars.schemas import (
    AVAILABILITY_SCOPES,
    CONSENT_VERSION,
    MAX_REVISION,
    CalendarCommand,
    CalendarGrantView,
    CalendarProvider,
    CalendarView,
)

MAX_GRANTS = 200


@dataclass(frozen=True)
class CalendarGrantSnapshot:
    id: UUID
    revision: int
    status: str
    connection_id: UUID | None
    credential_id: UUID | None
    external_account_id: str | None = field(repr=False)
    granted_scopes: frozenset[str]
    connection_status: str | None


@dataclass(frozen=True)
class CalendarScope:
    user_id: UUID
    workspace_id: UUID
    generation: int
    revision: int
    grants: tuple[CalendarGrantSnapshot, ...]


@dataclass
class Boundary:
    state: StudentCalendarState | None
    session: UserSession
    now: datetime

    @property
    def generation(self) -> int:
        return self.state.generation if self.state is not None else 0

    @property
    def revision(self) -> int:
        return self.state.revision if self.state is not None else 0


async def state(
    database: AsyncSession, user_id: UUID, workspace_id: UUID
) -> StudentCalendarState | None:
    row: StudentCalendarState | None = await database.scalar(
        select(StudentCalendarState)
        .where(
            StudentCalendarState.user_id == user_id,
            StudentCalendarState.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    return row


async def boundary(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    *,
    lock: bool = True,
    allow_paused: bool = False,
) -> Boundary:
    if lock:
        # Management/runtime serialize connector→membership→User→legacy. Take
        # the same ordered connector prefix before native student authority;
        # later credential metadata or mirror publication must not invert it.
        with database.no_autoflush:
            mirrors = list(
                await database.scalars(
                    select(ConnectorConnection.id)
                    .where(
                        ConnectorConnection.user_id == user_id,
                        ConnectorConnection.workspace_id == workspace_id,
                        ConnectorConnection.provider == "google",
                    )
                    .order_by(ConnectorConnection.id)
                    .limit(1001)
                    .with_for_update()
                )
            )
        if len(mirrors) > 1000:
            raise schedule.StudentError(409, "Too many calendar connections to check safely")
    user = await schedule.owner(database, user_id, workspace_id, lock=lock)
    query = select(UserSession).where(
        UserSession.user_id == user_id, UserSession.token_hash == session_hash
    )
    if lock:
        query = query.with_for_update()
    session = await database.scalar(query.execution_options(populate_existing=True))
    now = await database_now(database)
    if (
        session is None
        or session.client_type != "web"
        or session.revoked_at is not None
        or utc(session.expires_at) <= now
    ):
        raise schedule.StudentError(401, "Your sign-in is no longer available")
    if user.agent_paused and not allow_paused:
        raise schedule.StudentError(409, "Resume NavoX before checking calendar availability")
    return Boundary(await state(database, user_id, workspace_id), session, now)


async def ensure_state(
    database: AsyncSession, current: Boundary, user_id: UUID, workspace_id: UUID
) -> StudentCalendarState:
    if current.state is None:
        current.state = StudentCalendarState(
            user_id=user_id, workspace_id=workspace_id, generation=0, revision=0
        )
        database.add(current.state)
        await database.flush()
    return current.state


def check_command(current: Boundary, payload: CalendarCommand, *, admitting: bool = True) -> None:
    if payload.generation != current.generation or payload.revision != current.revision:
        raise schedule.StudentError(409, "Calendar permissions changed. Refresh and try again")
    if admitting and (current.generation >= MAX_REVISION or current.revision >= MAX_REVISION):
        raise schedule.StudentError(409, "Calendar permissions need review before another change")


def advance(row: StudentCalendarState) -> None:
    row.revision = min(MAX_REVISION, row.revision + 1)


async def grants(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, *, scheduling: bool = False
) -> list[StudentCalendarGrant | StudentCalendarWriteGrant]:
    model = StudentCalendarWriteGrant if scheduling else StudentCalendarGrant
    rows = list(
        await database.scalars(
            select(model)
            .where(
                model.user_id == user_id,
                model.workspace_id == workspace_id,
            )
            .order_by(model.accepted_at, model.id)
            .limit(MAX_GRANTS + 1)
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) > MAX_GRANTS:
        raise schedule.StudentError(409, "Too many calendar permission records to check safely")
    return cast(list[StudentCalendarGrant | StudentCalendarWriteGrant], rows)


async def owned_connection(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, connection_id: UUID | None
) -> Connection | None:
    if connection_id is None:
        return None
    row: Connection | None = await database.scalar(
        select(Connection)
        .where(
            Connection.id == connection_id,
            Connection.user_id == user_id,
            Connection.workspace_id == workspace_id,
            Connection.provider == "google",
        )
        .execution_options(populate_existing=True)
    )
    return row


def ready(connection: Connection | None) -> bool:
    return (
        connection is not None
        and connection.status == "active"
        and connection.credential_reference is not None
        and set(AVAILABILITY_SCOPES).issubset(connection.granted_scopes)
    )


async def scope_snapshot(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    *,
    lock: bool = True,
) -> CalendarScope:
    current = await boundary(database, user_id, workspace_id, session_hash, lock=lock)
    items = []
    for grant in await grants(database, user_id, workspace_id):
        if grant.status == "revoked":
            continue
        if (
            current.state is None
            or grant.owner_scope_id != current.state.id
            or grant.generation != current.generation
            or grant.consent_version != CONSENT_VERSION
        ):
            raise schedule.StudentError(409, "Calendar permissions need review")
        connection = await owned_connection(database, user_id, workspace_id, grant.connection_id)
        items.append(
            CalendarGrantSnapshot(
                id=grant.id,
                revision=grant.revision,
                status=grant.status,
                connection_id=connection.id if connection is not None else None,
                credential_id=connection.credential_reference if connection is not None else None,
                external_account_id=connection.external_account_id
                if connection is not None
                else None,
                granted_scopes=frozenset(connection.granted_scopes) if connection else frozenset(),
                connection_status=connection.status if connection else None,
            )
        )
    return CalendarScope(user_id, workspace_id, current.generation, current.revision, tuple(items))


async def check_scope(database: AsyncSession, snapshot: CalendarScope, session_hash: str) -> None:
    if (
        await scope_snapshot(
            database, snapshot.user_id, snapshot.workspace_id, session_hash, lock=True
        )
        != snapshot
    ):
        raise schedule.StudentError(409, "Calendar or sign-in permissions changed. Check again")


async def view(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    settings: Settings,
) -> CalendarView:
    current = await boundary(
        database, user_id, workspace_id, session_hash, lock=False, allow_paused=True
    )
    items = []
    for grant in await grants(database, user_id, workspace_id):
        connection = await owned_connection(database, user_id, workspace_id, grant.connection_id)
        status: Literal["pending", "connected", "unavailable", "revoked"] = (
            "revoked" if grant.status == "revoked" else "unavailable"
        )
        if grant.status == "pending":
            attempt = await database.get(OAuthAuthorizationAttempt, grant.oauth_attempt_id)
            if (
                attempt is not None
                and attempt.used_at is None
                and utc(attempt.expires_at) > current.now
            ):
                status = "pending"
        elif grant.status == "active" and ready(connection):
            status = "connected"
        items.append(
            CalendarGrantView(
                id=grant.id,
                connection_id=connection.id if connection else None,
                account_label=connection.external_email if connection else None,
                status=status,
                revision=grant.revision,
            )
        )
    configured = bool(
        settings.google_oauth_client_id.strip()
        and settings.google_oauth_client_secret
        and settings.google_oauth_client_secret.get_secret_value().strip()
    )
    try:
        CredentialVault(settings)
    except CredentialVaultError:
        configured = False
    return CalendarView(
        generation=current.generation,
        revision=current.revision,
        providers=[
            CalendarProvider(
                id="google", availability="available" if configured else "setup_pending"
            ),
            CalendarProvider(id="outlook", availability="unavailable"),
            CalendarProvider(id="icloud", availability="unavailable"),
        ],
        grants=items,
    )


async def retire(
    database: AsyncSession, grant: StudentCalendarGrant | StudentCalendarWriteGrant, now: datetime
) -> None:
    if grant.status == "revoked":
        return
    grant.status, grant.revoked_at = "revoked", now
    grant.revision = min(MAX_REVISION, grant.revision + 1)
    attempt = await database.get(OAuthAuthorizationAttempt, grant.oauth_attempt_id)
    if attempt is not None and attempt.used_at is None:
        attempt.used_at = now


async def disconnect(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    grant_id: UUID,
    payload: CalendarCommand,
    settings: Settings,
) -> CalendarView:
    current = await boundary(database, user_id, workspace_id, session_hash, allow_paused=True)
    grant = await database.scalar(
        select(StudentCalendarGrant)
        .where(
            StudentCalendarGrant.id == grant_id,
            StudentCalendarGrant.user_id == user_id,
            StudentCalendarGrant.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if grant is None:
        raise schedule.StudentError(404, "Calendar permission not found")
    if current.state is None or grant.owner_scope_id != current.state.id:
        raise schedule.StudentError(409, "Calendar permissions changed. Refresh and try again")
    if payload.generation != current.generation:
        raise schedule.StudentError(409, "Calendar permissions changed. Refresh and try again")
    if grant.status != "revoked":
        check_command(current, payload, admitting=False)
        await retire(database, grant, current.now)
        assert current.state is not None
        advance(current.state)
        schedule.audit(database, user_id, workspace_id, "student.calendar.disconnected", grant.id)
        await database.commit()
    return await view(database, user_id, workspace_id, session_hash, settings)


async def forget_calendar_data(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    """Caller holds User; retain reset fencing while removing purpose links only."""
    row = await state(database, user_id, workspace_id)
    if row is None:
        row = StudentCalendarState(
            user_id=user_id, workspace_id=workspace_id, generation=0, revision=0
        )
        database.add(row)
        await database.flush()
    now = await database_now(database)
    for grant in await grants(database, user_id, workspace_id):
        await retire(database, grant, now)
    for write_grant in await grants(database, user_id, workspace_id, scheduling=True):
        await retire(database, write_grant, now)
    # Unsent previews contain no operational side effect and can be erased.
    # Submitted/uncertain receipts remain held; reset must never convert an
    # unknown provider outcome into permission to send it again.
    await database.execute(
        delete(StudentCalendarEvent).where(
            StudentCalendarEvent.owner_scope_id == row.id,
            StudentCalendarEvent.status.in_(["pending", "failed"]),
        )
    )
    await database.execute(
        delete(StudentCalendarWriteGrant).where(StudentCalendarWriteGrant.owner_scope_id == row.id)
    )
    await database.flush()
    await database.execute(
        delete(StudentCalendarGrant).where(StudentCalendarGrant.owner_scope_id == row.id)
    )
    row.generation = min(MAX_REVISION, row.generation + 1)
    advance(row)
