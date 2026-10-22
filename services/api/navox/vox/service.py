"""Durable, content-free reservations. These are never media or model grants.

Every operation serializes with student reset and logout on the User row before
refreshing the exact authenticating web session and reading the database clock.
No client account header, UUID, or earlier dependency check replaces that proof.
"""

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.db.models import User, UserSession
from navox.db.vox import VoxCall, VoxState
from navox.student import service as schedule
from navox.vox.schemas import VoxCallView, VoxContext, VoxLease, VoxStart

LEASE_SECONDS = 120
TERMINAL_RETENTION = timedelta(hours=24)
MAX_CALLS_PER_OWNER = 200
MAX_GENERATION = 2147483647


class VoxError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Boundary:
    user: User
    session: UserSession
    state: VoxState | None
    now: datetime

    @property
    def generation(self) -> int:
        return self.state.generation if self.state is not None else 0


async def _state(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> VoxState | None:
    value: VoxState | None = await database.scalar(
        select(VoxState)
        .where(VoxState.user_id == user_id, VoxState.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    return value


def _advance(state: VoxState) -> None:
    # Saturation is terminal for new admission, never a wraparound to old proof.
    state.generation = min(MAX_GENERATION, state.generation + 1)


async def _ensure_state(
    database: AsyncSession, boundary: Boundary, user_id: UUID, workspace_id: UUID
) -> VoxState:
    if boundary.state is None:
        boundary.state = VoxState(user_id=user_id, workspace_id=workspace_id, generation=0)
        database.add(boundary.state)
        await database.flush()
    return boundary.state


def _finish(call: VoxCall, state: VoxState, now: datetime, reason: str) -> None:
    if call.status != "reserved":
        return
    call.status = "ended"
    call.end_reason = reason
    call.ended_at = now
    _advance(state)


async def _locked(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, session_token_hash: str
) -> Boundary:
    try:
        user = await schedule.owner(database, user_id, workspace_id, lock=True)
    except schedule.StudentError as error:
        raise VoxError(error.status_code, str(error)) from None
    session = await database.scalar(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.token_hash == session_token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = await database_now(database)
    if (
        session is None
        or session.client_type != "web"
        or session.revoked_at is not None
        or utc(session.expires_at) <= now
    ):
        raise VoxError(401, "Your sign-in is no longer available")
    state = await _state(database, user_id, workspace_id)
    boundary = Boundary(user, session, state, now)
    reserved = await database.scalar(
        select(VoxCall)
        .where(
            VoxCall.user_id == user_id,
            VoxCall.workspace_id == workspace_id,
            VoxCall.status == "reserved",
        )
        .execution_options(populate_existing=True)
    )
    if reserved is not None and utc(reserved.expires_at) <= now:
        state = await _ensure_state(database, boundary, user_id, workspace_id)
        _finish(reserved, state, now, "expired")
        from navox.vox.media import revoke_call_connections

        await revoke_call_connections(database, reserved.id, now=now, reason="expired")
        await database.flush()
    # Every terminal transition advances generation first. Deleting an old opaque
    # row therefore cannot make its original start or end command valid again.
    await database.execute(
        delete(VoxCall).where(
            VoxCall.user_id == user_id,
            VoxCall.workspace_id == workspace_id,
            VoxCall.status == "ended",
            VoxCall.ended_at <= now - TERMINAL_RETENTION,
        )
    )
    return boundary


async def _complete[T](database: AsyncSession, operation: Awaitable[T]) -> T:
    try:
        value = await operation
    except VoxError:
        # A rejected renewal may have just expired a reservation. Preserve that
        # safe terminal transition and its replay fence, then release the lock.
        await database.commit()
        raise
    await database.commit()
    return value


def _view(call: VoxCall) -> VoxCallView:
    return VoxCallView.model_validate(
        {
            "id": call.id,
            "client_call_id": call.client_call_id,
            "generation": call.generation,
            "status": call.status,
            "mode": "off-record",
            "created_at": utc(call.created_at),
            "expires_at": utc(call.expires_at),
            "ended_at": utc(call.ended_at) if call.ended_at is not None else None,
            "end_reason": call.end_reason,
            "media_enabled": False,
        }
    )


async def _call(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, client_call_id: UUID
) -> VoxCall | None:
    value: VoxCall | None = await database.scalar(
        select(VoxCall)
        .where(
            VoxCall.user_id == user_id,
            VoxCall.workspace_id == workspace_id,
            VoxCall.client_call_id == client_call_id,
        )
        .execution_options(populate_existing=True)
    )
    return value


async def _reserved(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> VoxCall | None:
    value: VoxCall | None = await database.scalar(
        select(VoxCall)
        .where(
            VoxCall.user_id == user_id,
            VoxCall.workspace_id == workspace_id,
            VoxCall.status == "reserved",
        )
        .execution_options(populate_existing=True)
    )
    return value


async def _count(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> int:
    return int(
        await database.scalar(
            select(func.count())
            .select_from(VoxCall)
            .where(VoxCall.user_id == user_id, VoxCall.workspace_id == workspace_id)
        )
        or 0
    )


def _admission(boundary: Boundary, generation: int) -> None:
    if boundary.user.agent_paused:
        raise VoxError(409, "Resume NavoX before reserving a call")
    if generation != boundary.generation or boundary.generation >= MAX_GENERATION:
        raise VoxError(409, "The call state changed. Check it again before starting")


async def context(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, session_token_hash: str
) -> VoxContext:
    async def operation() -> VoxContext:
        boundary = await _locked(database, user_id, workspace_id, session_token_hash)
        call = await _reserved(database, user_id, workspace_id)
        return VoxContext(
            generation=boundary.generation,
            call=_view(call) if call and call.auth_session_id == boundary.session.id else None,
            media_enabled=False,
        )

    return await _complete(database, operation())


async def start(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    payload: VoxStart,
) -> VoxCallView:
    async def operation() -> VoxCallView:
        boundary = await _locked(database, user_id, workspace_id, session_token_hash)
        call = await _call(database, user_id, workspace_id, payload.client_call_id)
        if call is not None:
            if call.auth_session_id != boundary.session.id:
                raise VoxError(404, "Call reservation not found")
            if call.status == "ended":
                raise VoxError(410, "This call reservation has ended")
            _admission(boundary, payload.generation)
            if call.generation != payload.generation:
                raise VoxError(409, "This call belongs to an earlier call state")
            # An exact start retry neither extends the lease nor allocates a room.
            return _view(call)
        _admission(boundary, payload.generation)
        if await _reserved(database, user_id, workspace_id) is not None:
            raise VoxError(409, "End the existing call before starting another")
        if await _count(database, user_id, workspace_id) >= MAX_CALLS_PER_OWNER:
            raise VoxError(429, "The call reservation limit was reached. Try again later")
        await _ensure_state(database, boundary, user_id, workspace_id)
        call = VoxCall(
            user_id=user_id,
            workspace_id=workspace_id,
            auth_session_id=boundary.session.id,
            client_call_id=payload.client_call_id,
            generation=boundary.generation,
            status="reserved",
            created_at=boundary.now,
            expires_at=min(
                boundary.now + timedelta(seconds=LEASE_SECONDS), utc(boundary.session.expires_at)
            ),
        )
        database.add(call)
        await database.flush()
        return _view(call)

    return await _complete(database, operation())


async def renew(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
    payload: VoxLease,
) -> VoxCallView:
    async def operation() -> VoxCallView:
        boundary = await _locked(database, user_id, workspace_id, session_token_hash)
        call = await _call(database, user_id, workspace_id, client_call_id)
        if call is None or call.auth_session_id != boundary.session.id:
            raise VoxError(404, "Call reservation not found")
        if call.status == "ended":
            raise VoxError(410, "This call reservation has ended")
        _admission(boundary, payload.generation)
        if call.generation != payload.generation:
            raise VoxError(409, "This call belongs to an earlier call state")
        call.expires_at = min(
            boundary.now + timedelta(seconds=LEASE_SECONDS), utc(boundary.session.expires_at)
        )
        await database.flush()
        return _view(call)

    return await _complete(database, operation())


async def end(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
    payload: VoxLease,
) -> None:
    async def operation() -> None:
        boundary = await _locked(database, user_id, workspace_id, session_token_hash)
        call = await _call(database, user_id, workspace_id, client_call_id)
        if call is not None:
            if call.auth_session_id != boundary.session.id:
                raise VoxError(404, "Call reservation not found")
            if call.status == "ended":
                return
            if call.generation != payload.generation:
                raise VoxError(409, "This call belongs to a different call state")
            state = await _ensure_state(database, boundary, user_id, workspace_id)
            _finish(call, state, boundary.now, "hang-up")
            from navox.vox.media import revoke_call_connections

            await revoke_call_connections(database, call.id, now=boundary.now, reason="hang-up")
            return
        if payload.generation != boundary.generation:
            return  # The absent command is already fenced; do not alter a newer call.
        state = await _ensure_state(database, boundary, user_id, workspace_id)
        reserved = await _reserved(database, user_id, workspace_id)
        if reserved is None:
            _advance(state)
        # End can arrive before start. Leave an opaque tombstone, without taking
        # away another call's generation or exhausting its ability to hang up.
        if await _count(database, user_id, workspace_id) < MAX_CALLS_PER_OWNER:
            database.add(
                VoxCall(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    auth_session_id=boundary.session.id,
                    client_call_id=client_call_id,
                    generation=payload.generation,
                    status="ended",
                    end_reason="hang-up",
                    created_at=boundary.now,
                    expires_at=boundary.now,
                    ended_at=boundary.now,
                )
            )

    await _complete(database, operation())


async def revoke_session_calls(
    database: AsyncSession, auth_session_id: UUID, *, now: datetime
) -> None:
    """Caller holds the session's User lock; commit with auth revocation, never here."""
    calls = list(
        await database.scalars(
            select(VoxCall)
            .where(VoxCall.auth_session_id == auth_session_id, VoxCall.status == "reserved")
            .execution_options(populate_existing=True)
        )
    )
    for call in calls:
        state = await _state(database, call.user_id, call.workspace_id)
        if state is None:
            state = VoxState(user_id=call.user_id, workspace_id=call.workspace_id, generation=0)
            database.add(state)
        _finish(call, state, now, "sign-out")
    from navox.vox.media import revoke_session_connections

    await revoke_session_connections(database, auth_session_id, now=now)
    await database.flush()


async def forget_vox_data(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    """Caller holds the User lock. Erase call metadata and retain only the epoch fence."""
    state = await _state(database, user_id, workspace_id)
    if state is None:
        state = VoxState(user_id=user_id, workspace_id=workspace_id, generation=0)
        database.add(state)
    _advance(state)
    from navox.vox.media import revoke_owned_connections

    await revoke_owned_connections(
        database, user_id, workspace_id, now=await database_now(database), reason="reset"
    )
    await database.execute(
        delete(VoxCall).where(VoxCall.user_id == user_id, VoxCall.workspace_id == workspace_id)
    )
    await database.flush()
