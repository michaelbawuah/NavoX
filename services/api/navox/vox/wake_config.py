"""Read-only, connection-bound delivery of explicitly approved browser wake config."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.models import UserSession
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.student import service as schedule
from navox.vox.service import MAX_GENERATION, VoxError

UNAVAILABLE = "Local wake listening is not available"


class WakeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_call_id: UUID
    connection_id: UUID
    generation: int
    expires_at: datetime
    engine: Literal["porcupine"] = "porcupine"
    access_key: str = Field(repr=False)
    keyword_public_path: str
    model_public_path: str
    label: Literal["Hey NavoX"] = "Hey NavoX"


def versioned_public_path(value: str, suffix: str) -> bool:
    """Only operator-set same-origin assets, without URL/query/traversal variants."""
    if (
        len(value) > 512
        or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
        or value.startswith("//")
        or "//" in value
        or not value.endswith(suffix)
    ):
        return False
    parts = value[1:].split("/")
    return (
        all(part not in {"", ".", ".."} for part in parts)
        and bool(re.match(r"[A-Za-z0-9]", parts[-1]))
        and any(re.search(r"(?:^|[._-])v[1-9][0-9]*(?:[._-]|$)", part) for part in parts)
    )


async def configuration(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
    connection_id: UUID,
    generation: int,
) -> WakeConfig:
    """Fresh owner/session/epoch checks, without renewals, writes or provider I/O."""
    if not (
        settings.student_vox_enabled
        and settings.student_vox_media_enabled
        and settings.student_vox_local_wake_enabled
    ):
        raise VoxError(404, UNAVAILABLE)
    if not settings.student_vox_porcupine_browser_credential_approved:
        raise VoxError(403, UNAVAILABLE)
    try:
        user = await schedule.owner(database, user_id, workspace_id, lock=True)
    except schedule.StudentError as error:
        raise VoxError(error.status_code, "The Vox wake request is not available") from None
    now = await database_now(database)
    session = await database.scalar(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.token_hash == session_token_hash,
            UserSession.client_type == "web",
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if session is None:
        raise VoxError(401, "Authentication required")
    if user.agent_paused:
        raise VoxError(409, "Resume NavoX before using local wake")
    connection = await database.scalar(
        select(VoxMediaConnection)
        .where(
            VoxMediaConnection.id == connection_id,
            VoxMediaConnection.user_id == user_id,
            VoxMediaConnection.workspace_id == workspace_id,
            VoxMediaConnection.auth_session_id == session.id,
            VoxMediaConnection.generation == generation,
        )
        .execution_options(populate_existing=True)
    )
    if connection is None:
        raise VoxError(404, "Media connection not found")
    call = await database.scalar(
        select(VoxCall)
        .where(
            VoxCall.id == connection.call_id,
            VoxCall.client_call_id == client_call_id,
            VoxCall.user_id == user_id,
            VoxCall.workspace_id == workspace_id,
            VoxCall.auth_session_id == session.id,
        )
        .execution_options(populate_existing=True)
    )
    if call is None:
        raise VoxError(404, "Media connection not found")
    state = await database.scalar(
        select(VoxState)
        .where(
            VoxState.id == connection.owner_scope_id,
            VoxState.user_id == user_id,
            VoxState.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    cleanup = await database.scalar(
        select(VoxMediaCleanup)
        .where(VoxMediaCleanup.connection_id == connection.id)
        .execution_options(populate_existing=True)
    )
    if (
        type(generation) is not int
        or not 0 <= generation < MAX_GENERATION
        or connection.status != "confirmed"
        or call.status != "reserved"
        or state is None
        or state.generation != generation
        or call.generation != generation
        or utc(connection.expires_at) <= now
        or utc(call.expires_at) <= now
        or utc(connection.expires_at) > min(utc(call.expires_at), utc(session.expires_at))
        or cleanup is None
        or cleanup.status != "armed"
        or cleanup.owner_scope_id != connection.owner_scope_id
        or cleanup.room_name != connection.room_name
        or cleanup.client_identity != connection.client_identity
        or cleanup.agent_identity != connection.agent_identity
        or utc(cleanup.connection_expires_at) != utc(connection.expires_at)
    ):
        raise VoxError(410, "This media connection has ended")
    key = settings.student_vox_porcupine_access_key
    key_value = key.get_secret_value() if key is not None else ""
    keyword = settings.student_vox_porcupine_keyword_public_path
    model = settings.student_vox_porcupine_model_public_path
    if (
        not key_value.strip()
        or len(key_value) > 1024
        or "\r" in key_value
        or "\n" in key_value
        or not versioned_public_path(keyword, ".ppn")
        or not versioned_public_path(model, ".pv")
    ):
        raise VoxError(503, UNAVAILABLE)
    return WakeConfig(
        client_call_id=client_call_id,
        connection_id=connection_id,
        generation=generation,
        expires_at=utc(connection.expires_at),
        access_key=key_value,
        keyword_public_path=keyword,
        model_public_path=model,
    )
