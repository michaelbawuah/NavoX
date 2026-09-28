"""Authenticated selected-state AI features. These routes cannot execute actions."""

from fastapi import APIRouter, HTTPException

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied
from navox.ai.domains import Domain
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.operational_service import OperationalRequest, OperationalResponse, advise
from navox.ai.runtime import GatewayUnavailable
from navox.ai.sessions import create_session
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.connectors.authorization import ConnectorAccessDenied

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/sessions", status_code=201)
async def new_session(
    account: CurrentAccountDependency, database: DatabaseSession
) -> dict[str, str]:
    try:
        session = await create_session(
            database, user_id=account.user.id, workspace_id=account.workspace.id
        )
        await database.commit()
    except ValueError:
        raise HTTPException(403, "Assistant session access is unavailable") from None
    return {"id": str(session.id)}


@router.post("/operations/{domain}", response_model=OperationalResponse)
async def operation(
    domain: Domain,
    payload: OperationalRequest,
    account: CurrentAccountDependency,
    settings: SettingsDependency,
) -> OperationalResponse:
    try:
        runtime = await build_runtime(settings)
        return await advise(
            runtime,
            settings,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            domain=domain,
            request=payload,
        )
    except (ContextDenied, ConnectorAccessDenied, PermissionError):
        raise HTTPException(
            403, "Selected information is unavailable or its permissions changed"
        ) from None
    except (AIProviderNotConfigured, GatewayUnavailable):
        raise HTTPException(503, "No qualified AI provider is available for this request") from None
    except ValueError:
        raise HTTPException(
            409, "Selected information or session changed; refresh and try again"
        ) from None
