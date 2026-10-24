"""Private, explicitly approved browser-only local wake configuration."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.vox import (
    PrivateVoxRoute,
    SessionHashDependency,
    bounded_json,
    expected_account,
    result,
    web_session_hash,
)
from navox.vox.schemas import VoxLease
from navox.vox.wake_config import WakeConfig, configuration

router = APIRouter(
    prefix="/vox/calls",
    tags=["vox local wake"],
    route_class=PrivateVoxRoute,
    dependencies=[Depends(web_session_hash), Depends(expected_account)],
)


@router.post(
    "/{client_call_id}/media/{connection_id}/wake-config",
    response_model=WakeConfig,
)
async def wake_config(
    client_call_id: UUID,
    connection_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    session_token_hash: SessionHashDependency,
    settings: SettingsDependency,
) -> WakeConfig:
    payload = await bounded_json(request, VoxLease)
    return await result(
        configuration(
            database,
            settings,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
            session_token_hash=session_token_hash,
            client_call_id=client_call_id,
            connection_id=connection_id,
            generation=payload.generation,
        )
    )
