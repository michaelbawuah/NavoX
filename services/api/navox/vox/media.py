"""Internal media intent and revocation ledger; no tokens, rooms or provider I/O.

An intent is not media admission. Its immutable locators are committed before a
future adapter may perform external work. Confirmation rereads authority after
that work and never extends a deadline or restores a revoked connection.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.db.models import User, UserSession, Workspace, WorkspaceMembership
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.vox import service as reservations

MAX_CONNECTIONS_PER_CALL = 200
MAX_SWEEP = 100


@dataclass(frozen=True)
class ConnectionIntent:
    connection_id: UUID
    reservation_id: UUID
    client_call_id: UUID
    owner_scope_id: UUID
    generation: int
    status: str
    created_at: datetime
    expires_at: datetime
    room_name: str
    client_identity: str
    agent_identity: str


def _intent(connection: VoxMediaConnection, call: VoxCall) -> ConnectionIntent:
    return ConnectionIntent(
        connection.id,
        call.id,
        call.client_call_id,
        connection.owner_scope_id,
        connection.generation,
        connection.status,
        utc(connection.created_at),
        utc(connection.expires_at),
        connection.room_name,
        connection.client_identity,
        connection.agent_identity,
    )


async def _cleanup(database: AsyncSession, connection: VoxMediaConnection) -> VoxMediaCleanup:
    row = await database.scalar(
        select(VoxMediaCleanup)
        .where(VoxMediaCleanup.connection_id == connection.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        # Recovery from a missing ledger never allocates new remote locators.
        row = VoxMediaCleanup(
            connection_id=connection.id,
            owner_scope_id=connection.owner_scope_id,
            room_name=connection.room_name,
            client_identity=connection.client_identity,
            agent_identity=connection.agent_identity,
            connection_expires_at=connection.expires_at,
            created_at=connection.created_at,
            status="armed",
            attempts=0,
        )
        database.add(row)
    return row


async def _revoke(
    database: AsyncSession,
    connection: VoxMediaConnection,
    now: datetime,
    reason: str,
    *,
    late_completion: bool = False,
) -> None:
    if connection.status != "revoked":
        connection.status = "revoked"
        connection.revoked_at = now
        connection.reason = reason
    cleanup = await _cleanup(database, connection)
    _request_cleanup(cleanup, now, late_completion=late_completion)
    await database.flush()


def _request_cleanup(
    cleanup: VoxMediaCleanup, now: datetime, *, late_completion: bool = False
) -> None:
    if cleanup.status == "armed" or late_completion:
        # A late external completion invalidates an earlier cleanup proof or an
        # in-flight claim: the new cleanup must happen after that completion.
        cleanup.status = "pending"
        cleanup.requested_at = cleanup.requested_at or now
        cleanup.next_attempt_at = now
        cleanup.claim_id = None
        cleanup.claim_expires_at = None
        cleanup.completed_at = None


async def _owned_connection(
    database: AsyncSession,
    boundary: reservations.Boundary,
    user_id: UUID,
    workspace_id: UUID,
    connection_id: UUID,
) -> VoxMediaConnection:
    connection = await database.scalar(
        select(VoxMediaConnection)
        .where(
            VoxMediaConnection.id == connection_id,
            VoxMediaConnection.user_id == user_id,
            VoxMediaConnection.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if connection is None or connection.auth_session_id != boundary.session.id:
        raise reservations.VoxError(404, "Media connection not found")
    return connection


async def _call(database: AsyncSession, connection: VoxMediaConnection) -> VoxCall | None:
    value: VoxCall | None = await database.scalar(
        select(VoxCall)
        .where(VoxCall.id == connection.call_id)
        .execution_options(populate_existing=True)
    )
    return value


async def _invalid_reason(
    database: AsyncSession,
    connection: VoxMediaConnection,
    user: User | None,
    now: datetime,
) -> str | None:
    if utc(connection.expires_at) <= now:
        return "expired"
    session = await database.scalar(
        select(UserSession)
        .where(UserSession.id == connection.auth_session_id)
        .execution_options(populate_existing=True)
    )
    if session is None or session.revoked_at is not None:
        return "sign-out"
    if utc(session.expires_at) <= now:
        return "expired"
    if user is None or session.user_id != connection.user_id or session.client_type != "web":
        return "authority-lost"
    if user.agent_paused:
        return "paused"
    call = await _call(database, connection)
    state = await database.scalar(
        select(VoxState)
        .where(VoxState.id == connection.owner_scope_id)
        .execution_options(populate_existing=True)
    )
    if (
        call is None
        or call.status != "reserved"
        or call.auth_session_id != connection.auth_session_id
        or call.user_id != connection.user_id
        or call.workspace_id != connection.workspace_id
        or call.generation != connection.generation
        or state is None
        or state.user_id != connection.user_id
        or state.workspace_id != connection.workspace_id
        or state.generation != connection.generation
    ):
        return "authority-lost"
    if utc(call.expires_at) <= now:
        return "expired"
    if utc(connection.expires_at) > min(utc(call.expires_at), utc(session.expires_at)):
        return "authority-lost"
    authorized = await database.scalar(
        select(WorkspaceMembership.user_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.user_id == connection.user_id,
            WorkspaceMembership.workspace_id == connection.workspace_id,
            WorkspaceMembership.role == "owner",
            Workspace.workspace_type == "personal",
        )
    )
    return "authority-lost" if authorized is None else None


async def begin_connection(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
    *,
    generation: int,
    request_id: UUID,
) -> ConnectionIntent:
    async def operation() -> ConnectionIntent:
        boundary = await reservations._locked(database, user_id, workspace_id, session_token_hash)
        if type(generation) is not int or not 0 <= generation < reservations.MAX_GENERATION:
            raise reservations.VoxError(422, "The media request is invalid")
        if not isinstance(request_id, UUID) or not isinstance(client_call_id, UUID):
            raise reservations.VoxError(422, "The media request is invalid")
        call = await reservations._call(database, user_id, workspace_id, client_call_id)
        if call is None or call.auth_session_id != boundary.session.id:
            raise reservations.VoxError(404, "Call reservation not found")
        if call.status != "reserved":
            raise reservations.VoxError(410, "This call reservation has ended")
        reservations._admission(boundary, generation)
        if call.generation != generation:
            raise reservations.VoxError(409, "This call belongs to an earlier call state")
        state = await reservations._ensure_state(database, boundary, user_id, workspace_id)
        previous = await database.scalar(
            select(VoxMediaConnection)
            .where(
                VoxMediaConnection.owner_scope_id == state.id,
                VoxMediaConnection.request_id == request_id,
            )
            .execution_options(populate_existing=True)
        )
        if previous is not None:
            if previous.call_id != call.id or previous.auth_session_id != boundary.session.id:
                raise reservations.VoxError(409, "The media request belongs to another call")
            if previous.status == "revoked":
                raise reservations.VoxError(410, "This media connection has ended")
            cleanup = await _cleanup(database, previous)
            fresh_now = await database_now(database)
            reason = await _invalid_reason(database, previous, boundary.user, fresh_now)
            if reason is not None or cleanup.status != "armed":
                await _revoke(database, previous, fresh_now, reason or "authority-lost")
                raise reservations.VoxError(410, "This media connection has ended")
            return _intent(previous, call)
        current = await database.scalar(
            select(VoxMediaConnection)
            .where(
                VoxMediaConnection.owner_scope_id == state.id,
                VoxMediaConnection.status.in_(("preparing", "confirmed")),
            )
            .execution_options(populate_existing=True)
        )
        if current is not None:
            reason = await _invalid_reason(database, current, boundary.user, boundary.now)
            if reason is not None:
                await _revoke(database, current, boundary.now, reason)
            else:
                raise reservations.VoxError(409, "Close the existing media connection first")
        if (
            await database.scalar(
                select(VoxMediaCleanup.id)
                .where(
                    VoxMediaCleanup.owner_scope_id == state.id,
                    VoxMediaCleanup.status != "done",
                )
                .limit(1)
            )
            is not None
        ):
            raise reservations.VoxError(409, "The earlier media connection is still closing")
        recent = list(
            await database.scalars(
                select(VoxMediaConnection.id)
                .where(VoxMediaConnection.call_id == call.id)
                .limit(MAX_CONNECTIONS_PER_CALL)
            )
        )
        if len(recent) >= MAX_CONNECTIONS_PER_CALL:
            raise reservations.VoxError(429, "The media connection limit was reached")
        epoch = uuid4()
        connection = VoxMediaConnection(
            id=epoch,
            user_id=user_id,
            workspace_id=workspace_id,
            auth_session_id=boundary.session.id,
            call_id=call.id,
            owner_scope_id=state.id,
            request_id=request_id,
            generation=generation,
            status="preparing",
            room_name="vx-" + epoch.hex,
            client_identity="vc-" + epoch.hex,
            agent_identity="va-" + epoch.hex,
            created_at=boundary.now,
            expires_at=min(
                boundary.now + timedelta(seconds=reservations.LEASE_SECONDS),
                utc(call.expires_at),
                utc(boundary.session.expires_at),
            ),
        )
        database.add(connection)
        await database.flush()
        await _cleanup(database, connection)
        await database.flush()
        return _intent(connection, call)

    return await reservations._complete(database, operation())


async def confirm_connection(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    connection_id: UUID,
) -> ConnectionIntent:
    """Fresh web proof only; external adapters report completion via finalize_provision."""

    async def operation() -> ConnectionIntent:
        boundary = await reservations._locked(database, user_id, workspace_id, session_token_hash)
        connection = await _owned_connection(
            database, boundary, user_id, workspace_id, connection_id
        )
        cleanup = await _cleanup(database, connection)
        fresh_now = await database_now(database)
        reason = await _invalid_reason(database, connection, boundary.user, fresh_now)
        if connection.status == "revoked" or reason is not None or cleanup.status != "armed":
            await _revoke(
                database, connection, fresh_now, reason or "authority-lost", late_completion=True
            )
            raise reservations.VoxError(410, "This media connection has ended")
        call = await _call(database, connection)
        assert call is not None  # Rechecked above while retaining the shared User lock.
        if connection.status == "preparing":
            connection.status = "confirmed"
            connection.confirmed_at = fresh_now
        await database.flush()
        return _intent(connection, call)

    return await reservations._complete(database, operation())


async def finalize_provision(database: AsyncSession, intent: ConnectionIntent) -> ConnectionIntent:
    """Trusted adapter completion, including after web auth or owned rows disappear.

    This is not an HTTP entry point and a snapshot alone never grants authority.
    Future adapters must call it even when their originating request is cancelled.
    The independent ledger can retire late effects after logout, reset or deletion;
    active confirmation additionally requires fresh live User/session/call proof.
    """

    async def operation() -> ConnectionIntent:
        initial = await database.scalar(
            select(VoxMediaConnection)
            .where(VoxMediaConnection.id == intent.connection_id)
            .execution_options(populate_existing=True)
        )
        owner_id = (
            initial.user_id
            if initial is not None
            else await database.scalar(
                select(VoxState.user_id).where(VoxState.id == intent.owner_scope_id)
            )
        )
        user = None
        if owner_id is not None:
            user = await database.scalar(
                select(User)
                .where(User.id == owner_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        connection = await database.scalar(
            select(VoxMediaConnection)
            .where(VoxMediaConnection.id == intent.connection_id)
            .execution_options(populate_existing=True)
        )
        cleanup = await database.scalar(
            select(VoxMediaCleanup)
            .where(VoxMediaCleanup.connection_id == intent.connection_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cleanup is None or (
            cleanup.owner_scope_id != intent.owner_scope_id
            or cleanup.room_name != intent.room_name
            or cleanup.client_identity != intent.client_identity
            or cleanup.agent_identity != intent.agent_identity
            or utc(cleanup.created_at) != intent.created_at
            or utc(cleanup.connection_expires_at) != intent.expires_at
        ):
            raise reservations.VoxError(422, "The provision completion is invalid")
        fresh_now = await database_now(database)
        if connection is None:
            _request_cleanup(cleanup, fresh_now, late_completion=True)
            await database.flush()
            raise reservations.VoxError(410, "This media connection has ended")
        if (
            connection.call_id != intent.reservation_id
            or connection.owner_scope_id != intent.owner_scope_id
            or connection.generation != intent.generation
            or connection.room_name != intent.room_name
            or connection.client_identity != intent.client_identity
            or connection.agent_identity != intent.agent_identity
            or utc(connection.created_at) != intent.created_at
            or utc(connection.expires_at) != intent.expires_at
        ):
            raise reservations.VoxError(422, "The provision completion is invalid")
        reason = await _invalid_reason(database, connection, user, fresh_now)
        if connection.status == "revoked" or reason is not None or cleanup.status != "armed":
            await _revoke(
                database, connection, fresh_now, reason or "authority-lost", late_completion=True
            )
            raise reservations.VoxError(410, "This media connection has ended")
        call = await _call(database, connection)
        if call is None or call.client_call_id != intent.client_call_id:
            await _revoke(database, connection, fresh_now, "authority-lost", late_completion=True)
            raise reservations.VoxError(410, "This media connection has ended")
        if connection.status == "preparing":
            connection.status = "confirmed"
            connection.confirmed_at = fresh_now
        await database.flush()
        return _intent(connection, call)

    return await reservations._complete(database, operation())


async def revoke_connection(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    connection_id: UUID,
) -> None:
    async def operation() -> None:
        boundary = await reservations._locked(database, user_id, workspace_id, session_token_hash)
        connection = await _owned_connection(
            database, boundary, user_id, workspace_id, connection_id
        )
        await _revoke(database, connection, boundary.now, "hang-up")

    await reservations._complete(database, operation())


async def revoke_call_connections(
    database: AsyncSession, call_id: UUID, *, now: datetime, reason: str
) -> None:
    """Caller retains the User lock and commits with its reservation transition."""
    connections = list(
        await database.scalars(
            select(VoxMediaConnection)
            .where(VoxMediaConnection.call_id == call_id, VoxMediaConnection.status != "revoked")
            .execution_options(populate_existing=True)
        )
    )
    for connection in connections:
        await _revoke(database, connection, now, reason)


async def revoke_owned_connections(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, *, now: datetime, reason: str
) -> None:
    """Caller retains the User lock; the independent ledger survives subsequent deletion."""
    connections = list(
        await database.scalars(
            select(VoxMediaConnection)
            .where(
                VoxMediaConnection.user_id == user_id,
                VoxMediaConnection.workspace_id == workspace_id,
                VoxMediaConnection.status != "revoked",
            )
            .execution_options(populate_existing=True)
        )
    )
    for connection in connections:
        await _revoke(database, connection, now, reason)


async def revoke_session_connections(
    database: AsyncSession, auth_session_id: UUID, *, now: datetime
) -> None:
    """Caller holds the session owner's User lock; no independent commit."""
    connections = list(
        await database.scalars(
            select(VoxMediaConnection)
            .where(
                VoxMediaConnection.auth_session_id == auth_session_id,
                VoxMediaConnection.status != "revoked",
            )
            .execution_options(populate_existing=True)
        )
    )
    for connection in connections:
        await _revoke(database, connection, now, "sign-out")


async def revoke_user_connections(
    database: AsyncSession, user_id: UUID, *, now: datetime, reason: str
) -> None:
    """A global user pause retires media in every owned workspace under its User lock."""
    connections = list(
        await database.scalars(
            select(VoxMediaConnection)
            .where(
                VoxMediaConnection.user_id == user_id,
                VoxMediaConnection.status != "revoked",
            )
            .execution_options(populate_existing=True)
        )
    )
    for connection in connections:
        await _revoke(database, connection, now, reason)


async def sweep_connections(database: AsyncSession, *, limit: int = MAX_SWEEP) -> int:
    """Bounded revocation discovery; never connects a provider or starts a worker."""
    if type(limit) is not int or not 1 <= limit <= MAX_SWEEP:
        raise ValueError("Media sweep limit must be between 1 and 100")
    now = await database_now(database)
    owned = exists(
        select(WorkspaceMembership.user_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.user_id == VoxMediaConnection.user_id,
            WorkspaceMembership.workspace_id == VoxMediaConnection.workspace_id,
            WorkspaceMembership.role == "owner",
            Workspace.workspace_type == "personal",
        )
    )
    candidates = list(
        (
            await database.execute(
                select(VoxMediaConnection.id, VoxMediaConnection.user_id)
                .outerjoin(User, User.id == VoxMediaConnection.user_id)
                .outerjoin(UserSession, UserSession.id == VoxMediaConnection.auth_session_id)
                .outerjoin(VoxCall, VoxCall.id == VoxMediaConnection.call_id)
                .outerjoin(VoxState, VoxState.id == VoxMediaConnection.owner_scope_id)
                .where(
                    VoxMediaConnection.status.in_(("preparing", "confirmed")),
                    or_(
                        VoxMediaConnection.expires_at <= now,
                        User.id.is_(None),
                        User.agent_paused.is_(True),
                        UserSession.id.is_(None),
                        UserSession.revoked_at.is_not(None),
                        UserSession.expires_at <= now,
                        UserSession.client_type != "web",
                        UserSession.user_id != VoxMediaConnection.user_id,
                        VoxCall.id.is_(None),
                        VoxCall.status != "reserved",
                        VoxCall.expires_at <= now,
                        VoxCall.auth_session_id != VoxMediaConnection.auth_session_id,
                        VoxCall.generation != VoxMediaConnection.generation,
                        VoxState.id.is_(None),
                        VoxState.generation != VoxMediaConnection.generation,
                        ~owned,
                    ),
                )
                .order_by(VoxMediaConnection.expires_at, VoxMediaConnection.id)
                .limit(limit)
            )
        ).all()
    )
    revoked = 0
    # Cross-account login replacements already lock User IDs in this order.
    for connection_id, user_id in sorted(candidates, key=lambda row: row[1].int):
        user = await database.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        connection = await database.scalar(
            select(VoxMediaConnection)
            .where(VoxMediaConnection.id == connection_id)
            .execution_options(populate_existing=True)
        )
        if connection is None or connection.status == "revoked":
            continue
        fresh_now = await database_now(database)
        reason = await _invalid_reason(database, connection, user, fresh_now)
        if reason is not None:
            await _revoke(database, connection, fresh_now, reason)
            revoked += 1
    await database.commit()
    return revoked
