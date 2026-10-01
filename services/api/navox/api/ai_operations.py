"""Authenticated selected-state AI features. These routes cannot execute actions."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied
from navox.ai.domains import Domain
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.intent_plan import IntentPlanRequest, IntentPlanResponse, plan_intents
from navox.ai.operational_service import OperationalRequest, OperationalResponse, advise
from navox.ai.runtime import GatewayUnavailable
from navox.ai.sessions import create_session
from navox.ai.speech_adapters import configured_speech_adapters
from navox.ai.speech_service import (
    SpeechOversized,
    SpeechQuotaExceeded,
    SpeechRequestRejected,
    SpeechUnavailable,
    SpeechUpstreamFailure,
    read_bounded_audio,
    transcribe_audio,
)
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.connectors.authorization import ConnectorAccessDenied

router = APIRouter(prefix="/ai", tags=["ai"])


class TranscriptionResponse(BaseModel):
    """Only bounded question text. A transcript never conveys action authority."""

    text: str


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


@router.post("/assistant/intents", response_model=IntentPlanResponse)
async def assistant_intents(
    payload: IntentPlanRequest,
    account: CurrentAccountDependency,
    settings: SettingsDependency,
) -> IntentPlanResponse:
    """One registered intent plan. This route can look things up, never act."""

    try:
        runtime = await build_runtime(settings)
        return await plan_intents(
            runtime,
            settings,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            request=payload,
        )
    except (ContextDenied, ConnectorAccessDenied, PermissionError):
        raise HTTPException(
            403, "Intent planning is unavailable or its permissions changed"
        ) from None
    except (AIProviderNotConfigured, GatewayUnavailable):
        raise HTTPException(503, "No qualified AI provider is available for this request") from None
    except ValueError:
        raise HTTPException(409, "The conversation changed; refresh and try again") from None


@router.post("/assistant/speech/transcribe", response_model=TranscriptionResponse)
async def assistant_transcribe(
    request: Request,
    response: Response,
    account: CurrentAccountDependency,
    settings: SettingsDependency,
) -> TranscriptionResponse:
    """One bounded recorded clip in, bounded question text out. No action authority."""

    require_origin(request, settings.web_origin)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if media_type != "audio/wav":
        raise HTTPException(415, "A raw audio/wav body is required")
    try:
        audio = await read_bounded_audio(request.stream())
    except SpeechOversized:
        raise HTTPException(413, "The audio clip is too large") from None
    try:
        runtime = await build_runtime(settings)
        text = await transcribe_audio(
            runtime,
            settings,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            audio=audio,
            adapters=configured_speech_adapters(settings),
        )
    except SpeechRequestRejected:
        raise HTTPException(422, "The audio clip is not a supported recording") from None
    except SpeechQuotaExceeded:
        raise HTTPException(429, "The transcription quota is exhausted") from None
    except PermissionError:
        raise HTTPException(403, "Speech transcription is unavailable") from None
    except SpeechUpstreamFailure:
        raise HTTPException(502, "The speech provider failed") from None
    except (SpeechUnavailable, AIProviderNotConfigured, GatewayUnavailable, LookupError):
        raise HTTPException(503, "No qualified speech provider is available") from None
    response.headers["Cache-Control"] = "no-store"
    return TranscriptionResponse(text=text)
