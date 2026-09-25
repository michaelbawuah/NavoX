"""Owner-bound Canvas OAuth setup and reconnection. Never collect account passwords."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.api.auth import (
    CurrentAccount,
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
)
from navox.api.connector_management import require_origin
from navox.connectors import canvas_oauth
from navox.connectors.builtin.oauth_canvas import OAUTH_CANVAS_MANIFEST, CanvasConfig
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.dispatcher import dispatch_connector_sync
from navox.connectors.jobs import ConnectorSyncWork
from navox.connectors.provenance import ensure_provenance_connection
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    OAuthAuthorizationAttempt,
    User,
    WorkspaceMembership,
)

router = APIRouter(prefix="/connectors/canvas-lms", tags=["Canvas"])


class ConnectCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    capabilities: list[str] = Field(min_length=1, max_length=5)
    confirmed: bool

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("Explicit authorization is required")
        return True

    @field_validator("capabilities")
    @classmethod
    def reads_only(cls, value: list[str]) -> list[str]:
        return sorted(canvas_oauth.validate_capabilities(value))


def configured(settings: Settings) -> canvas_oauth.CanvasDeployment:
    try:
        result = canvas_oauth.deployment(settings)
        SecretBroker(settings)
        return result
    except (ConnectorRuntimeError, SecretBrokerError):
        raise HTTPException(
            503, "Canvas needs an institution-approved OAuth developer key and encrypted storage"
        ) from None


@router.get("/setup")
async def setup(
    account: CurrentAccountDependency, settings: SettingsDependency
) -> dict[str, object]:
    del account
    config = configured(settings)
    return {
        "origin": config.origin,
        "capabilities": list(canvas_oauth.CANVAS_SCOPES),
        "read_only": True,
    }


async def start(
    account: CurrentAccount,
    database: AsyncSession,
    settings: Settings,
    capabilities: list[str],
    connection_id: UUID | None = None,
) -> dict[str, object]:
    config = configured(settings)
    selected = sorted(canvas_oauth.validate_capabilities(capabilities))
    now = await database_now(database)
    owner = await database.scalar(select(User).where(User.id == account.user.id).with_for_update())
    member = await database.get(
        WorkspaceMembership, (account.workspace.id, account.user.id), populate_existing=True
    )
    if owner is None or member is None:
        raise HTTPException(403, "Canvas setup is not authorized")
    recent = await database.scalar(
        select(func.count())
        .select_from(OAuthAuthorizationAttempt)
        .where(
            OAuthAuthorizationAttempt.user_id == owner.id,
            OAuthAuthorizationAttempt.provider == "canvas",
            OAuthAuthorizationAttempt.created_at > now - timedelta(minutes=10),
        )
    )
    if recent and recent >= 10:
        raise HTTPException(429, "Wait before starting another Canvas authorization")
    provenance_id = None
    if connection_id is not None:
        row = await database.scalar(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.workspace_id == account.workspace.id,
                ConnectorConnection.user_id == owner.id,
                ConnectorConnection.provider == "canvas",
            )
            .with_for_update()
        )
        if row is None or row.status == "DISCONNECTED":
            raise HTTPException(404, "Canvas connection not found")
        if frozenset(selected) != frozenset(row.authorized_capabilities):
            raise HTTPException(409, "Reconnect cannot add permissions")
        provenance_id = (await ensure_provenance_connection(database, row)).id
    state = token_urlsafe(32)
    expires = now + timedelta(minutes=10)
    database.add(
        OAuthAuthorizationAttempt(
            user_id=owner.id,
            workspace_id=account.workspace.id,
            provider="canvas",
            purpose="reconnect" if connection_id else "connect",
            connection_id=provenance_id,
            requested_scopes=selected,
            state_hash=sha256(state.encode()).hexdigest(),
            code_verifier=config.fingerprint,
            expires_at=expires,
        )
    )
    await database.commit()
    return {
        "authorization_url": canvas_oauth.authorization_url(config, state, selected),
        "expires_at": expires,
        "requested_scopes": sorted({canvas_oauth.CANVAS_SCOPES[c] for c in selected}),
    }


async def require_connect_setup(settings: SettingsDependency) -> None:
    try:
        configured(settings)
    except HTTPException:
        # Preserve the existing unavailable-catalogue response before validating
        # a setup command. Configured deployments require explicit consent below.
        raise HTTPException(409, "Canvas setup is not enabled for this deployment") from None


@router.post("/connect", dependencies=[Depends(require_connect_setup)])
async def connect(
    command: ConnectCommand,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    return await start(account, database, settings, command.capabilities)


async def queue(
    database: AsyncSession, connection: ConnectorConnection, request_id: UUID, settings: Settings
) -> str:
    payload = ConnectorSyncWork(
        str(connection.id),
        str(connection.user_id),
        str(connection.workspace_id),
        str(request_id),
        "manual",
    )
    await database.commit()
    if settings.ai_provider == "disabled":
        return "pending"
    try:
        await dispatch_connector_sync(payload, settings=settings, initial=True)
        return "queued"
    except Exception:
        return "pending"


@router.get("/callback")
async def callback(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> RedirectResponse:
    config = configured(settings)
    values = request.query_params
    state, code = values.get("state", ""), values.get("code", "")
    if not 16 <= len(state) <= 128 or any(
        len(values.getlist(k)) > 1 for k in ("state", "code", "error")
    ):
        raise HTTPException(400, "Invalid Canvas authorization callback")
    now = await database_now(database)
    attempt = await database.scalar(
        select(OAuthAuthorizationAttempt)
        .where(
            OAuthAuthorizationAttempt.state_hash == sha256(state.encode()).hexdigest(),
            OAuthAuthorizationAttempt.provider == "canvas",
            OAuthAuthorizationAttempt.user_id == account.user.id,
            OAuthAuthorizationAttempt.workspace_id == account.workspace.id,
            OAuthAuthorizationAttempt.used_at.is_(None),
            OAuthAuthorizationAttempt.expires_at > now,
        )
        .with_for_update()
    )
    if attempt is None or attempt.code_verifier != config.fingerprint:
        raise HTTPException(400, "Canvas authorization expired or belongs to another session")
    capabilities = list(attempt.requested_scopes)
    canvas_oauth.validate_capabilities(capabilities)
    original = attempt.connection_id
    fingerprint = attempt.code_verifier
    # One-use state is committed BEFORE token exchange; replay can never reuse a code.
    claimed = await database.scalar(
        update(OAuthAuthorizationAttempt)
        .where(
            OAuthAuthorizationAttempt.id == attempt.id, OAuthAuthorizationAttempt.used_at.is_(None)
        )
        .values(used_at=now)
        .returning(OAuthAuthorizationAttempt.id)
    )
    if claimed is None:
        raise HTTPException(400, "Canvas authorization was already used")
    await database.commit()
    if values.get("error") or not 1 <= len(code) <= 4096:
        raise HTTPException(400, "Canvas authorization was not completed")
    try:
        tokens = await canvas_oauth.exchange(config, code=code)
    except ConnectorRuntimeError:
        raise HTTPException(502, "Canvas authorization failed; reconnect to try again") from None
    if tokens.refresh is None:
        raise HTTPException(502, "Canvas did not provide renewable authorization")
    # Reload authority after network I/O; login at start is not permanent authority.
    user = await database.scalar(
        select(User)
        .where(User.id == account.user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    member = await database.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == account.workspace.id,
            WorkspaceMembership.user_id == account.user.id,
        )
        .with_for_update()
    )
    if user is None or member is None or configured(settings).fingerprint != fingerprint:
        raise HTTPException(403, "Canvas authorization is no longer permitted")
    external = f"{config.origin}:{tokens.user_id}"
    row = await database.scalar(
        select(ConnectorConnection)
        .where(
            ConnectorConnection.workspace_id == account.workspace.id,
            ConnectorConnection.user_id == user.id,
            ConnectorConnection.provider == "canvas",
            ConnectorConnection.external_account_id == external,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if original is not None and (row is None or row.legacy_connection_id != original):
        raise HTTPException(409, "Reconnect must use the same Canvas account")
    if row is not None and row.status == "DISCONNECTED":
        raise HTTPException(409, "This Canvas connection was disconnected")
    definition = await database.scalar(
        select(ConnectorDefinition).where(
            ConnectorDefinition.connector_key == "canvas-lms",
            ConnectorDefinition.version == "1.1.0",
        )
    )
    if definition is None:
        try:
            async with database.begin_nested():
                definition = ConnectorDefinition(
                    id=uuid5(NAMESPACE_URL, "navox:canvas-lms:1.1.0"),
                    connector_key="canvas-lms",
                    version="1.1.0",
                    display_name="Canvas LMS",
                    connector_class="OAUTH_API",
                    trust_level="NAVOX_FIRST_PARTY",
                    manifest=OAUTH_CANVAS_MANIFEST.model_dump(mode="json", by_alias=True),
                )
                database.add(definition)
                await database.flush()
        except IntegrityError:
            definition = await database.scalar(
                select(ConnectorDefinition).where(
                    ConnectorDefinition.connector_key == "canvas-lms",
                    ConnectorDefinition.version == "1.1.0",
                )
            )
    if definition is None or not definition.active:
        raise HTTPException(409, "Canvas connector is unavailable")
    if row is None:
        row = ConnectorConnection(
            id=uuid5(NAMESPACE_URL, f"navox:canvas:{account.workspace.id}:{user.id}:{external}"),
            connector_definition_id=definition.id,
            workspace_id=account.workspace.id,
            user_id=user.id,
            provider="canvas",
            external_account_id=external,
            display_name=f"{config.origin} · Canvas account {tokens.user_id}",
            status="CONNECTED",
            health_state="CONNECTED",
            config={},
            authorized_capabilities=[],
            provider_capabilities=[],
        )
        database.add(row)
        await database.flush()
    elif original is not None and frozenset(capabilities) != frozenset(row.authorized_capabilities):
        raise HTTPException(409, "Permissions changed during reconnection; start again")
    row.connector_definition_id = definition.id
    row.config = CanvasConfig(
        base_url=config.origin,
        canvas_user_id=tokens.user_id,
        deployment_hash=fingerprint,
        owner_id=user.id,
        capabilities=capabilities,
    ).model_dump(mode="json")
    row.authorized_capabilities = capabilities
    row.provider_capabilities = capabilities
    row.health_state = "PAUSED" if row.status == "PAUSED" else "CONNECTED"
    row.last_error_code = None
    row.retry_not_before = None
    # Credential replacement fences any old in-flight work, including pause/resume races.
    row.sync_generation += 1
    row.sync_lease_token = None
    row.sync_lease_expires_at = None
    try:
        await SecretBroker(settings).store(
            database,
            {"CANVAS_REFRESH_TOKEN": tokens.refresh.get_secret_value()},
            connection_id=row.id,
            workspace_id=row.workspace_id,
            user_id=user.id,
        )
    except SecretBrokerError:
        raise HTTPException(503, "Canvas authorization could not be stored securely") from None
    await ensure_provenance_connection(database, row)
    database.add(
        AuditEvent(
            user_id=user.id,
            workspace_id=row.workspace_id,
            event_type="connector.canvas.authorized",
            actor_type="user",
            entity_type="connector_connection",
            entity_id=row.id,
            event_metadata={"capabilities": capabilities, "reconnected": original is not None},
        )
    )
    if row.status == "PAUSED" or user.agent_paused:
        await database.commit()
    else:
        await queue(database, row, claimed, settings)
    return RedirectResponse(
        settings.web_origin.rstrip("/") + "/?canvas=connected",
        status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
