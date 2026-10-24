"Session-bound Outlook consent, encrypted reviews and non-repeating sends."

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from typing import Any
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.configured import build_runtime
from navox.ai.context import ContextBuilder
from navox.ai.features import configured_secrets, source_task
from navox.ai.foundation.contracts import LatencyClass, Profile, TaskType
from navox.communication.validation import generated_draft
from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.contracts import CanonicalResource, ConnectorManifest, stable_resource_id
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    OAuthAuthorizationAttempt,
    User,
    UserSession,
    Workspace,
    WorkspaceMembership,
)
from navox.db.outlook import OutlookMailDraft
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.intelligence.extraction import source_document_hash
from navox.outlook.cleanup import lock_for_reset
from navox.outlook.schemas import Connect, Content, Generate, Preview, Revise, Send
from navox.providers.microsoft_mail import (
    READ_CAPABILITY,
    READ_SCOPES,
    SEND_CAPABILITY,
    MailIdentity,
    MailMessage,
    MicrosoftMailError,
    MicrosoftMailGateway,
    action_reason,
    authorization_url,
    build_mime,
    oauth_endpoint,
    receipt_fingerprint,
)

KEY = "microsoft-outlook"
MANIFEST = ConnectorManifest.model_validate(
    {
        "id": KEY,
        "version": "1.0.0",
        "displayName": "Outlook email",
        "category": "productivity",
        "connectorClass": "OAUTH_API",
        "auth": [
            {"kind": "oauth2", "label": "Microsoft OAuth", "scopes": [*READ_SCOPES, "Mail.Send"]}
        ],
        "resourceTypes": ["communication.message"],
        "capabilities": {
            "read": [
                {
                    "name": READ_CAPABILITY,
                    "description": "Read owner-consented Outlook messages",
                    "sensitive": True,
                }
            ],
            "write": [
                {
                    "name": SEND_CAPABILITY,
                    "description": (
                        "Send only a versioned exact reply explicitly approved by its owner"
                    ),
                    "sensitive": True,
                }
            ],
            "events": [],
            "incrementalSync": False,
        },
        "requiredSecrets": ["MICROSOFT_REFRESH_TOKEN"],
        "rateLimitStrategy": "provider_headers",
        "minimumNavoxConnectorApiVersion": "1",
    }
)


class OutlookError(ValueError):
    def __init__(self, message: str, status_code: int = 409) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class Scope:
    user_id: UUID
    workspace_id: UUID
    session_hash: str


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def now() -> datetime:
    return datetime.now(UTC)


def configured(settings: Settings) -> bool:
    if not settings.microsoft_oauth_client_id or settings.microsoft_oauth_client_secret is None:
        return False
    if not settings.microsoft_oauth_client_secret.get_secret_value():
        return False
    try:
        oauth_endpoint(settings, "token")
        SecretBroker(settings)
    except (MicrosoftMailError, SecretBrokerError):
        return False
    return True


async def authority(
    database: AsyncSession, scope: Scope, *, processing: bool = True
) -> UserSession:
    # Match shared lifecycle/reset: connectors → membership → User. Taking the
    # same order avoids a send/disconnect lock inversion, including multiple mailboxes.
    locked_connectors = await lock_for_reset(
        database, user_id=scope.user_id, workspace_id=scope.workspace_id
    )
    member = await database.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == scope.workspace_id,
            WorkspaceMembership.user_id == scope.user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = await database.scalar(
        select(User)
        .where(User.id == scope.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session = await database.scalar(
        select(UserSession)
        .where(
            UserSession.user_id == scope.user_id,
            UserSession.token_hash == scope.session_hash,
            UserSession.client_type == "web",
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now(),
        )
        .execution_options(populate_existing=True)
    )
    workspace = await database.get(Workspace, scope.workspace_id, populate_existing=True)
    if (
        user is None
        or session is None
        or member is None
        or member.role != "owner"
        or workspace is None
        or workspace.workspace_type != "personal"
    ):
        raise OutlookError("Sign in again to use this Outlook account", 401)
    current_connectors = set(
        await database.scalars(
            select(ConnectorConnection.id).where(
                ConnectorConnection.user_id == scope.user_id,
                ConnectorConnection.workspace_id == scope.workspace_id,
                ConnectorConnection.provider == "microsoft",
            )
        )
    )
    if current_connectors != set(locked_connectors):
        # A first-mailbox callback may insert after an empty pre-lock snapshot.
        # Fail without acquiring that new connector while holding membership/User.
        raise OutlookError("Outlook connections changed. Reopen Inbox and try again", 409)
    if processing and user.agent_paused:
        raise OutlookError("NavoX is paused. Resume it before reading or sending email", 403)
    return session


async def connection(
    database: AsyncSession,
    scope: Scope,
    connection_id: UUID,
    *,
    active: bool = True,
    send: bool = False,
) -> ConnectorConnection:
    await authority(database, scope, processing=active)
    try:
        row = await owned_connector(
            database,
            connection_id=connection_id,
            workspace_id=scope.workspace_id,
            user_id=scope.user_id,
            require_active=active,
        )
    except ConnectorAccessDenied:
        raise OutlookError(
            "This Outlook connection is unavailable. Reconnect it in Connections", 403
        ) from None
    definition = await database.get(
        ConnectorDefinition, row.connector_definition_id, populate_existing=True
    )
    if row.provider != "microsoft" or definition is None or definition.connector_key != KEY:
        raise OutlookError("This Outlook connection is unavailable", 404)
    if active:
        required = {READ_CAPABILITY, SEND_CAPABILITY} if send else {READ_CAPABILITY}
        scopes = row.config.get("scopes")
        if (
            not isinstance(scopes, list)
            or "Mail.Read" not in scopes
            or (send and "Mail.Send" not in scopes)
        ):
            raise OutlookError("Reconnect Outlook with the required email permission", 403)
        if (
            not required.issubset(row.authorized_capabilities)
            or not required.issubset(row.provider_capabilities)
            or row.credential_reference is None
        ):
            raise OutlookError("Reconnect Outlook with the required email permission", 403)
        provenance = (
            await database.get(Connection, row.legacy_connection_id, populate_existing=True)
            if row.legacy_connection_id
            else None
        )
        if (
            provenance is None
            or provenance.status != "active"
            or (provenance.user_id, provenance.workspace_id) != (scope.user_id, scope.workspace_id)
            or provenance.provider != "microsoft"
            or provenance.external_account_id != f"connector:{row.id}"
            or provenance.credential_reference != row.credential_reference
            or "Mail.Read" not in provenance.granted_scopes
            or (send and "Mail.Send" not in provenance.granted_scopes)
        ):
            raise OutlookError("This Outlook connection is unavailable", 403)
    return row


def mailbox(row: ConnectorConnection) -> str:
    value = row.config.get("mailbox")
    if not isinstance(value, str) or not value:
        raise OutlookError("Reconnect Outlook to verify this mailbox", 403)
    return value


def mailbox_binding(row: ConnectorConnection) -> str | None:
    account = row.config.get("account_id")
    if not isinstance(account, str) or not account:
        return None
    # This digest contains no mailbox address or message text. It keeps an
    # uncertain send non-repeatable after Forget replaces a connection UUID.
    return sha256(f"microsoft:{row.user_id}:{row.workspace_id}:{account}".encode()).hexdigest()


def audit(database: AsyncSession, scope: Scope, kind: str, identifier: UUID) -> None:
    database.add(
        AuditEvent(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            event_type="outlook." + kind,
            actor_type="user",
            entity_type="outlook",
            entity_id=identifier,
            event_metadata={},
        )
    )


async def status(database: AsyncSession, settings: Settings, scope: Scope) -> dict[str, object]:
    await authority(database, scope, processing=False)
    rows = (
        await database.scalars(
            select(ConnectorConnection)
            .join(ConnectorDefinition)
            .where(
                ConnectorConnection.workspace_id == scope.workspace_id,
                ConnectorConnection.user_id == scope.user_id,
                ConnectorConnection.provider == "microsoft",
                ConnectorDefinition.connector_key == KEY,
            )
            .order_by(ConnectorConnection.created_at)
        )
    ).all()
    user = await database.get(User, scope.user_id, populate_existing=True)
    available = configured(settings)
    views = []
    for row in rows:
        if not mailbox_binding(row) or not row.config.get("mailbox"):
            continue  # Generic Forget erased this disconnected provider identity.
        definition = await database.get(
            ConnectorDefinition, row.connector_definition_id, populate_existing=True
        )
        provenance = (
            await database.get(Connection, row.legacy_connection_id, populate_existing=True)
            if row.legacy_connection_id
            else None
        )
        scopes = row.config.get("scopes")
        ready = (
            available
            and definition is not None
            and definition.active
            and user is not None
            and not user.agent_paused
            and row.status in {"CONNECTED", "DEGRADED"}
            and row.health_state not in {"PAUSED", "DISCONNECTED"}
            and row.credential_reference is not None
            and provenance is not None
            and provenance.status == "active"
            and provenance.provider == "microsoft"
            and provenance.external_account_id == f"connector:{row.id}"
            and provenance.credential_reference == row.credential_reference
            and (provenance.user_id, provenance.workspace_id) == (scope.user_id, scope.workspace_id)
            and isinstance(scopes, list)
            and "Mail.Read" in scopes
            and "Mail.Read" in provenance.granted_scopes
        )
        views.append(
            {
                "id": str(row.id),
                "email": mailbox(row),
                "status": row.status,
                "read_enabled": ready
                and READ_CAPABILITY in row.authorized_capabilities
                and READ_CAPABILITY in row.provider_capabilities,
                "send_enabled": ready
                and isinstance(scopes, list)
                and "Mail.Send" in scopes
                and provenance is not None
                and "Mail.Send" in provenance.granted_scopes
                and SEND_CAPABILITY in row.authorized_capabilities
                and SEND_CAPABILITY in row.provider_capabilities,
            }
        )
    return {
        "configured": available,
        "connections": views,
        "notice": (
            "Reading and sending are separate permissions. Every reply needs your "
            "review and explicit Send."
        )
        if available
        else (
            "Outlook connection setup is not available here yet. You can try another "
            "email provider or check back later."
        ),
    }


async def start_connect(
    database: AsyncSession, settings: Settings, scope: Scope, payload: Connect
) -> dict[str, object]:
    session = await authority(database, scope, processing=False)
    if not configured(settings):
        raise OutlookError("Outlook connection setup is pending for this deployment", 503)
    target = (
        await connection(database, scope, payload.connection_id, active=False)
        if payload.connection_id
        else None
    )
    if payload.send and target is None:
        raise OutlookError("Connect Outlook for reading before adding Send permission", 422)
    requested = [*READ_SCOPES, *(["Mail.Send"] if payload.send else [])]
    state, verifier = token_urlsafe(32), token_urlsafe(64)
    attempt = OAuthAuthorizationAttempt(
        user_id=scope.user_id,
        workspace_id=scope.workspace_id,
        provider="microsoft",
        purpose=f"outlook:{session.id}",
        connection_id=target.legacy_connection_id if target else None,
        requested_scopes=requested,
        state_hash=sha256(state.encode()).hexdigest(),
        code_verifier=verifier,
        expires_at=now() + timedelta(minutes=10),
    )
    database.add(attempt)
    url = authorization_url(
        settings,
        state=state,
        verifier=verifier,
        scopes=requested,
        login_hint=mailbox(target) if target else None,
    )
    await database.commit()
    return {"authorization_url": url, "requested_scopes": requested}


def same_identity(row: ConnectorConnection, identity: MailIdentity) -> bool:
    return (
        row.external_account_id == identity.subject
        and row.config.get("account_id") == identity.account_id
    )


async def finish_connect(
    database: AsyncSession, settings: Settings, scope: Scope, *, state: str, code: str
) -> None:
    session = await authority(database, scope, processing=False)
    if not configured(settings) or not 20 <= len(state) <= 200 or not 1 <= len(code) <= 8192:
        raise OutlookError("This Outlook sign-in is invalid. Start again from Connections", 400)
    purpose = f"outlook:{session.id}"
    attempt = await database.scalar(
        select(OAuthAuthorizationAttempt)
        .where(
            OAuthAuthorizationAttempt.state_hash == sha256(state.encode()).hexdigest(),
            OAuthAuthorizationAttempt.user_id == scope.user_id,
            OAuthAuthorizationAttempt.workspace_id == scope.workspace_id,
            OAuthAuthorizationAttempt.provider == "microsoft",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        attempt is None
        or attempt.used_at is not None
        or utc(attempt.expires_at) <= now()
        or attempt.purpose != purpose
    ):
        raise OutlookError("This Outlook sign-in expired or was already used. Start again", 409)
    attempt.used_at = now()
    attempt_id, verifier = attempt.id, attempt.code_verifier
    requested, target_id = set(attempt.requested_scopes), attempt.connection_id
    await database.commit()  # One-shot before calling Microsoft; replay never exchanges a code.
    gateway = MicrosoftMailGateway()
    tokens = await gateway.token(settings, code=code, verifier=verifier)
    identity = await gateway.identity(tokens.access_token)
    granted = tokens.scopes.intersection(requested)
    if not {"User.Read", "Mail.Read"}.issubset(granted) or (
        "Mail.Send" in requested and "Mail.Send" not in granted
    ):
        raise OutlookError("Microsoft did not grant the required Outlook permission", 403)
    await authority(database, scope, processing=False)
    fresh = await database.get(OAuthAuthorizationAttempt, attempt_id, populate_existing=True)
    if (
        fresh is None
        or fresh.purpose != purpose
        or fresh.used_at is None
        or utc(fresh.expires_at) <= now()
    ):
        raise OutlookError("This Outlook sign-in was cancelled. Start again", 409)
    definition = await database.scalar(
        select(ConnectorDefinition).where(
            ConnectorDefinition.connector_key == KEY,
            ConnectorDefinition.version == MANIFEST.version,
        )
    )
    if definition is None:
        definition = ConnectorDefinition(
            connector_key=KEY,
            version=MANIFEST.version,
            display_name="Outlook email",
            connector_class="OAUTH_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest=MANIFEST.model_dump(mode="json", by_alias=True),
        )
        database.add(definition)
        await database.flush()
    elif not definition.active:
        raise OutlookError("Outlook connections are unavailable in this deployment", 403)
    if target_id is not None:
        target = await database.scalar(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.legacy_connection_id == target_id,
                ConnectorConnection.workspace_id == scope.workspace_id,
                ConnectorConnection.user_id == scope.user_id,
                ConnectorConnection.connector_definition_id == definition.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if target is None or not same_identity(target, identity):
            raise OutlookError("Use the same Microsoft mailbox when reconnecting Outlook", 409)
    else:
        target = await database.scalar(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.workspace_id == scope.workspace_id,
                ConnectorConnection.connector_definition_id == definition.id,
                ConnectorConnection.external_account_id == identity.subject,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if target is not None and (
            target.user_id != scope.user_id or not same_identity(target, identity)
        ):
            raise OutlookError("This Microsoft mailbox cannot be connected here", 409)
    provenance: Connection | None
    if target is None:
        connector_id = uuid4()
        provenance = Connection(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            provider="microsoft",
            external_account_id=f"connector:{connector_id}",
            external_email=identity.email,
            status="active",
            granted_scopes=sorted(granted),
        )
        database.add(provenance)
        await database.flush()
        target = ConnectorConnection(
            id=connector_id,
            connector_definition_id=definition.id,
            legacy_connection_id=provenance.id,
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            provider="microsoft",
            external_account_id=identity.subject,
            display_name="Outlook email",
            config={},
            sync_generation=0,
        )
        database.add(target)
        await database.flush()
    else:
        provenance = (
            await database.get(Connection, target.legacy_connection_id, populate_existing=True)
            if target.legacy_connection_id
            else None
        )
        if provenance is None:
            raise OutlookError("This Outlook connection needs review", 409)
    capabilities = [READ_CAPABILITY, *([SEND_CAPABILITY] if "Mail.Send" in granted else [])]
    target.config = {
        "mailbox": identity.email,
        "account_id": identity.account_id,
        "scopes": sorted(granted),
        "ai_sensitivity": "PERSONAL",
    }
    target.authorized_capabilities = capabilities
    target.provider_capabilities = capabilities
    target.status = target.health_state = "CONNECTED"
    target.paused_at = None
    target.last_error_code = None
    target.last_healthy_at = now()
    target.sync_generation += 1
    provenance.status = "active"
    provenance.external_email = identity.email
    provenance.granted_scopes = sorted(granted)
    await database.flush()
    await SecretBroker(settings).store(
        database,
        {"MICROSOFT_REFRESH_TOKEN": tokens.refresh_token},
        connection_id=target.id,
        workspace_id=scope.workspace_id,
        user_id=scope.user_id,
    )
    audit(database, scope, "connected", target.id)
    await database.commit()


async def disconnect(
    database: AsyncSession, settings: Settings, scope: Scope, connection_id: UUID
) -> None:
    row = await connection(database, scope, connection_id, active=False)
    # Cancels even an already exchanged attempt still waiting for Microsoft's identity.
    await database.execute(
        update(OAuthAuthorizationAttempt)
        .where(
            OAuthAuthorizationAttempt.user_id == scope.user_id,
            OAuthAuthorizationAttempt.workspace_id == scope.workspace_id,
            OAuthAuthorizationAttempt.provider == "microsoft",
        )
        .values(purpose="outlook-cancelled")
    )
    if row.credential_reference is not None:
        try:
            await SecretBroker(settings).delete_for_connection(
                database,
                connection_id=row.id,
                workspace_id=scope.workspace_id,
                user_id=scope.user_id,
            )
        except SecretBrokerError:
            # An unavailable encryption key must never make revocation impossible.
            # Detach only this owner's references; do not delete an unproven bundle.
            credential_id = row.credential_reference
            row.credential_reference = None
            provenance = (
                await database.get(Connection, row.legacy_connection_id, populate_existing=True)
                if row.legacy_connection_id
                else None
            )
            if (
                provenance is not None
                and (provenance.user_id, provenance.workspace_id)
                == (scope.user_id, scope.workspace_id)
                and provenance.credential_reference == credential_id
            ):
                provenance.credential_reference = None
    row.status = row.health_state = "DISCONNECTED"
    row.authorized_capabilities = []
    row.provider_capabilities = []
    row.sync_generation += 1
    if row.legacy_connection_id:
        provenance = await database.get(
            Connection, row.legacy_connection_id, populate_existing=True
        )
        if provenance:
            provenance.status = "disconnected"
            provenance.granted_scopes = []
    await database.execute(
        update(OutlookMailDraft)
        .where(
            OutlookMailDraft.connection_id == row.id,
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
        )
        .values(
            status=case(
                (OutlookMailDraft.status == "review", "discarded"),
                (OutlookMailDraft.status == "executing", "uncertain"),
                else_=OutlookMailDraft.status,
            ),
            encrypted_payload="",
        )
    )
    audit(database, scope, "disconnected", row.id)
    await database.commit()


async def access(
    database: AsyncSession,
    settings: Settings,
    scope: Scope,
    connection_id: UUID,
    *,
    send: bool = False,
) -> tuple[str, int]:
    if not configured(settings):
        raise OutlookError("Outlook connection setup is pending for this deployment", 503)
    row = await connection(database, scope, connection_id, send=send)
    broker = SecretBroker(settings)
    with await broker.lease(
        database,
        connection_id=row.id,
        workspace_id=scope.workspace_id,
        user_id=scope.user_id,
        purpose="communication.send" if send else "sync.read",
        names={"MICROSOFT_REFRESH_TOKEN"},
    ) as lease:
        tokens = await MicrosoftMailGateway().token(
            settings, refresh=lease.get("MICROSOFT_REFRESH_TOKEN")
        )
    identity = await MicrosoftMailGateway().identity(tokens.access_token)
    if not same_identity(row, identity) or identity.email.casefold() != mailbox(row).casefold():
        raise OutlookError(
            "The Microsoft mailbox changed. Reconnect Outlook before continuing", 403
        )
    previous_scopes = row.config.get("scopes")
    if not isinstance(previous_scopes, list) or not all(
        isinstance(item, str) for item in previous_scopes
    ):
        raise OutlookError("Reconnect Outlook before continuing", 403)
    granted = tokens.scopes.intersection(previous_scopes)
    required = {"User.Read", "Mail.Read", *(["Mail.Send"] if send else [])}
    if not required.issubset(granted):
        raise OutlookError("Outlook permission changed. Reconnect before continuing", 403)
    # Rotation is encrypted and committed in the same owner/connection transaction.
    await broker.store(
        database,
        {"MICROSOFT_REFRESH_TOKEN": tokens.refresh_token},
        connection_id=row.id,
        workspace_id=scope.workspace_id,
        user_id=scope.user_id,
    )
    generation = row.sync_generation
    await database.commit()
    return tokens.access_token, generation


async def recheck(
    database: AsyncSession,
    scope: Scope,
    connection_id: UUID,
    generation: int,
    *,
    send: bool = False,
) -> ConnectorConnection:
    row = await connection(database, scope, connection_id, send=send)
    if row.sync_generation != generation:
        raise OutlookError("Outlook permissions changed. Reopen the email and review again", 409)
    return row


async def read_message(
    database: AsyncSession, settings: Settings, scope: Scope, connection_id: UUID, message_id: str
) -> tuple[ConnectorConnection, MailMessage, int]:
    token, generation = await access(database, settings, scope, connection_id)
    message = await MicrosoftMailGateway().message(token, message_id)
    row = await recheck(database, scope, connection_id, generation)
    return row, message, generation


async def messages(
    database: AsyncSession, settings: Settings, scope: Scope, connection_id: UUID
) -> dict[str, object]:
    token, generation = await access(database, settings, scope, connection_id)
    found, has_more = await MicrosoftMailGateway().messages(token)
    row = await recheck(database, scope, connection_id, generation)
    replied = set(
        (
            await database.scalars(
                select(OutlookMailDraft.source_message_id).where(
                    OutlookMailDraft.user_id == scope.user_id,
                    OutlookMailDraft.workspace_id == scope.workspace_id,
                    OutlookMailDraft.mailbox_binding == mailbox_binding(row),
                    OutlookMailDraft.status.in_(("executing", "uncertain", "completed")),
                )
            )
        ).all()
    )
    candidates = []
    for message in found:
        if message.id in replied:
            continue
        reason = action_reason(message, mailbox(row))
        if reason:
            candidates.append(
                {
                    "id": message.id,
                    "sender": message.sender,
                    "subject": message.subject or "(No subject)",
                    "preview": message.preview[:240],
                    "sent_at": message.sent_at.isoformat(),
                    "open_url": message.open_url,
                    "reason": reason,
                }
            )
    return {
        "messages": candidates,
        "has_more": has_more,
        "notice": (
            "Possible requests and your follow-up flags from the latest 50 Inbox "
            "messages. Review the email to decide what needs a reply. Older mail and "
            "later replies are not automatically monitored."
        ),
    }


def message_view(message: MailMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "sender": message.sender,
        "to": message.recipient,
        "subject": message.subject or "(No subject)",
        "body": message.body[:24000],
        "sent_at": message.sent_at.isoformat(),
        "open_url": message.open_url,
        "truncated": len(message.body) > 24000,
    }


def ciphers(settings: Settings) -> list[Fernet]:
    configured_keys = (
        settings.connector_secret_encryption_key,
        settings.google_token_encryption_key,
    )
    result = []
    for key in configured_keys:
        if key is not None and key.get_secret_value():
            try:
                result.append(Fernet(key.get_secret_value().encode()))
            except ValueError:
                raise OutlookError("Private Outlook draft storage is unavailable", 503) from None
    if not result:
        raise OutlookError("Private Outlook draft storage is unavailable", 503)
    return result


def payload_binding(draft: OutlookMailDraft) -> dict[str, object]:
    return {
        "draft": str(draft.id),
        "user": str(draft.user_id),
        "workspace": str(draft.workspace_id),
        "connection": str(draft.connection_id),
        "mailbox": draft.mailbox_binding,
        "source": draft.source_message_id,
        "revision": draft.revision,
    }


def save_content(settings: Settings, draft: OutlookMailDraft, content: dict[str, object]) -> None:
    payload = {**payload_binding(draft), "content": content}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    draft.payload_hash = sha256(encoded).hexdigest()
    draft.encrypted_payload = ciphers(settings)[0].encrypt(encoded).decode()
    draft.expires_at = now() + timedelta(minutes=15)
    draft.payload_expires_at = now() + timedelta(days=7)


def content_of(settings: Settings, draft: OutlookMailDraft) -> dict[str, Any]:
    if not draft.encrypted_payload or utc(draft.payload_expires_at) <= now():
        raise OutlookError("This draft content expired or was cleared. Create a new review", 409)
    if len(draft.encrypted_payload) > 150_000:
        raise OutlookError("This draft needs to be created again", 409)
    parsed: object = None
    for cipher in ciphers(settings):
        try:
            raw = cipher.decrypt(draft.encrypted_payload.encode())
            if not compare_digest(sha256(raw).hexdigest(), draft.payload_hash):
                raise OutlookError("This draft changed. Create a new review", 409)
            parsed = json.loads(raw)
            break
        except (InvalidToken, ValueError, UnicodeError):
            continue
    if (
        not isinstance(parsed, dict)
        or any(parsed.get(key) != value for key, value in payload_binding(draft).items())
        or not isinstance(parsed.get("content"), dict)
    ):
        raise OutlookError("This private draft could not be opened. Create it again", 409)
    content: dict[str, Any] = parsed["content"]
    if not all(
        isinstance(content.get(key), str)
        for key in ("sender", "to", "subject", "body", "source_hash", "in_reply_to")
    ) or not isinstance(content.get("generation"), int):
        raise OutlookError("This private draft is invalid. Create it again", 409)
    return content


def draft_view(
    settings: Settings, draft: OutlookMailDraft, *, connection_id: UUID | None = None
) -> dict[str, object]:
    available = bool(draft.encrypted_payload) and utc(draft.payload_expires_at) > now()
    content = (
        content_of(settings, draft)
        if available
        else {"sender": "", "to": "", "subject": "Reply content cleared", "body": ""}
    )
    notices = {
        "review": (
            "Review the recipient and message, then choose Send. Approval lasts 15 "
            "minutes; saved draft content is available for 7 days. Calendar "
            "availability is not rechecked automatically."
        ),
        "executing": "The send outcome is being checked. Do not send another copy.",
        "uncertain": (
            "The send outcome is unconfirmed. Check the outcome or inspect Sent in "
            "Outlook; this draft cannot be sent again."
        ),
        "completed": (
            "The matching message was verified in Outlook Sent. This does not prove "
            "recipient delivery; later replies are not monitored."
        ),
    }
    return {
        "id": str(draft.id),
        "revision": draft.revision,
        "payload_hash": draft.payload_hash,
        "connection_id": str(connection_id or draft.connection_id),
        "source_message_id": draft.source_message_id,
        "sender": content["sender"],
        "to": content["to"],
        "subject": content["subject"],
        "body": content["body"],
        "status": draft.status,
        "expires_at": utc(draft.expires_at).isoformat(),
        "verified_at": utc(draft.verified_at).isoformat() if draft.verified_at else None,
        "content_available": available,
        "notice": notices.get(draft.status, "This draft is unavailable.")
        + (
            (" Private reply content was cleared; only a content-free send receipt remains.")
            if not available
            else ""
        ),
    }


async def owned_draft(database: AsyncSession, scope: Scope, draft_id: UUID) -> OutlookMailDraft:
    await authority(database, scope, processing=False)
    row = await database.scalar(
        select(OutlookMailDraft)
        .where(
            OutlookMailDraft.id == draft_id,
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status == "discarded":
        raise OutlookError("This Outlook draft is no longer available", 404)
    if row.encrypted_payload and utc(row.payload_expires_at) <= now():
        row.encrypted_payload = ""
        if row.status == "review":
            row.status = "discarded"
        await database.commit()
        if row.status == "discarded":
            raise OutlookError("This saved draft expired. Open its email to start again", 404)
    return row


def reviewed_content(
    row: ConnectorConnection, message: MailMessage, payload: Content, generation: int
) -> dict[str, object]:
    sender = mailbox(row)
    if message.recipient.casefold() == sender.casefold():
        raise OutlookError("An email you sent cannot authorize a reply to yourself", 422)
    return {
        "sender": sender,
        "to": message.recipient,
        "subject": payload.subject,
        "body": payload.body,
        "source_hash": message.source_hash,
        "in_reply_to": message.internet_message_id,
        "generation": generation,
    }


async def list_drafts(
    database: AsyncSession, settings: Settings, scope: Scope
) -> list[dict[str, object]]:
    await authority(database, scope, processing=False)
    await database.execute(
        update(OutlookMailDraft)
        .where(
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
            OutlookMailDraft.payload_expires_at <= now(),
            OutlookMailDraft.encrypted_payload != "",
        )
        .values(
            encrypted_payload="",
            status=case(
                (OutlookMailDraft.status == "review", "discarded"), else_=OutlookMailDraft.status
            ),
        )
    )
    await database.commit()
    await authority(database, scope, processing=False)
    rows = (
        await database.scalars(
            select(OutlookMailDraft)
            .where(
                OutlookMailDraft.user_id == scope.user_id,
                OutlookMailDraft.workspace_id == scope.workspace_id,
                OutlookMailDraft.status != "discarded",
            )
            .order_by(OutlookMailDraft.created_at.desc())
            .limit(100)
        )
    ).all()
    connections = (
        await database.scalars(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.user_id == scope.user_id,
                ConnectorConnection.workspace_id == scope.workspace_id,
                ConnectorConnection.provider == "microsoft",
                ConnectorConnection.status.in_(("CONNECTED", "DEGRADED")),
            )
            .order_by(ConnectorConnection.created_at)
        )
    ).all()
    current = {mailbox_binding(row): row.id for row in connections if mailbox_binding(row)}
    return [
        draft_view(settings, row, connection_id=current.get(row.mailbox_binding)) for row in rows
    ]


async def preview(
    database: AsyncSession, settings: Settings, scope: Scope, payload: Preview
) -> dict[str, object]:
    await connection(database, scope, payload.connection_id)
    request_hash = sha256(payload.model_dump_json().encode()).hexdigest()
    existing = await database.scalar(
        select(OutlookMailDraft).where(
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
            OutlookMailDraft.request_id == payload.request_id,
        )
    )
    if existing:
        if existing.request_hash != request_hash or existing.status == "discarded":
            raise OutlookError("This review request already belongs to a different draft", 409)
        return draft_view(settings, existing)
    await database.commit()
    row, message, generation = await read_message(
        database, settings, scope, payload.connection_id, payload.source_message_id
    )
    # A concurrent preview using the same key may have completed while Graph read.
    existing = await database.scalar(
        select(OutlookMailDraft)
        .where(
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
            OutlookMailDraft.request_id == payload.request_id,
        )
        .execution_options(populate_existing=True)
    )
    if existing:
        if existing.request_hash != request_hash or existing.status == "discarded":
            raise OutlookError("This review request already belongs to a different draft", 409)
        return draft_view(settings, existing)
    active_source = await database.scalar(
        select(OutlookMailDraft.id)
        .where(
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
            OutlookMailDraft.mailbox_binding == mailbox_binding(row),
            OutlookMailDraft.source_message_id == payload.source_message_id,
            OutlookMailDraft.status != "discarded",
        )
        .limit(1)
    )
    if active_source is not None:
        raise OutlookError(
            (
                "This email already has a draft or send record. Open that record before "
                "creating another reply"
            ),
            409,
        )
    binding = mailbox_binding(row)
    if binding is None:
        raise OutlookError("Reconnect Outlook before saving this draft", 403)
    draft = OutlookMailDraft(
        id=uuid4(),
        user_id=scope.user_id,
        workspace_id=scope.workspace_id,
        connection_id=row.id,
        source_message_id=message.id,
        request_id=payload.request_id,
        request_hash=request_hash,
        revision=1,
        status="review",
        mailbox_binding=binding,
    )
    save_content(settings, draft, reviewed_content(row, message, payload, generation))
    database.add(draft)
    audit(database, scope, "draft_created", draft.id)
    await database.commit()
    return draft_view(settings, draft)


async def revise(
    database: AsyncSession, settings: Settings, scope: Scope, draft_id: UUID, payload: Revise
) -> dict[str, object]:
    draft = await owned_draft(database, scope, draft_id)
    if draft.status != "review" or draft.revision != payload.expected_revision:
        raise OutlookError("This draft changed or was already sent. Reopen it before editing", 409)
    connection_id, source_id = draft.connection_id, draft.source_message_id
    await database.commit()
    row, message, generation = await read_message(
        database, settings, scope, connection_id, source_id
    )
    draft = await owned_draft(database, scope, draft_id)
    if draft.status != "review" or draft.revision != payload.expected_revision:
        raise OutlookError("This draft changed. Reopen it before editing", 409)
    draft.revision += 1
    save_content(settings, draft, reviewed_content(row, message, payload, generation))
    audit(database, scope, "draft_revised", draft.id)
    await database.commit()
    return draft_view(settings, draft)


async def discard(database: AsyncSession, scope: Scope, draft_id: UUID) -> None:
    draft = await owned_draft(database, scope, draft_id)
    if draft.status != "review":
        raise OutlookError("Check this send outcome before discarding its record", 409)
    draft.status = "discarded"
    draft.encrypted_payload = ""
    audit(database, scope, "draft_discarded", draft.id)
    await database.commit()


def mime_for(draft: OutlookMailDraft, content: dict[str, Any]) -> bytes:
    if not draft.internet_message_id:
        raise OutlookError("This draft has no send receipt to check", 409)
    return build_mime(
        sender=content["sender"],
        to=content["to"],
        subject=content["subject"],
        body=content["body"],
        message_id=draft.internet_message_id,
        in_reply_to=content["in_reply_to"],
    )


async def send(
    database: AsyncSession, settings: Settings, scope: Scope, draft_id: UUID, payload: Send
) -> dict[str, object]:
    draft = await owned_draft(database, scope, draft_id)
    if draft.revision != payload.expected_revision or not compare_digest(
        draft.payload_hash, payload.expected_payload_hash
    ):
        raise OutlookError("This draft changed. Review its latest version before sending", 409)
    if draft.status in {"executing", "uncertain", "completed"}:
        # Neither duplicate requests nor a fresh request UUID can retransmit.
        return draft_view(settings, draft)
    if draft.status != "review" or utc(draft.expires_at) <= now():
        raise OutlookError(
            "This review expired. Save and review the draft again before sending", 409
        )
    content = content_of(settings, draft)
    connection_id, source_id = draft.connection_id, draft.source_message_id
    await database.commit()
    token, generation = await access(database, settings, scope, connection_id, send=True)
    message = await MicrosoftMailGateway().message(token, source_id)
    row = await recheck(database, scope, connection_id, generation, send=True)
    draft = await owned_draft(database, scope, draft_id)
    if draft.status != "review":
        return draft_view(settings, draft)
    if (
        draft.revision != payload.expected_revision
        or not compare_digest(draft.payload_hash, payload.expected_payload_hash)
        or utc(draft.expires_at) <= now()
    ):
        raise OutlookError("This draft changed or expired. Review it again before sending", 409)
    content = content_of(settings, draft)
    if (
        content["generation"] != generation
        or content["source_hash"] != message.source_hash
        or content["to"].casefold() != message.recipient.casefold()
        or content["sender"].casefold() != mailbox(row).casefold()
    ):
        raise OutlookError(
            "The source email or Outlook permission changed. Save and review again", 409
        )
    other = await database.scalar(
        select(OutlookMailDraft.id).where(
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
            OutlookMailDraft.send_request_id == payload.request_id,
            OutlookMailDraft.id != draft.id,
        )
    )
    if other is not None:
        raise OutlookError("This send request belongs to another draft", 409)
    previous_send = await database.scalar(
        select(OutlookMailDraft.id)
        .where(
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
            OutlookMailDraft.mailbox_binding == draft.mailbox_binding,
            OutlookMailDraft.source_message_id == draft.source_message_id,
            OutlookMailDraft.id != draft.id,
            OutlookMailDraft.status.in_(("executing", "uncertain", "completed")),
        )
        .limit(1)
    )
    if previous_send is not None:
        raise OutlookError(
            "A reply to this email already has a send record. Check that outcome first", 409
        )
    message_id = f"<navox-{draft.id.hex}-{draft.revision}@mail.navox.app>"
    mime = build_mime(
        sender=content["sender"],
        to=content["to"],
        subject=content["subject"],
        body=content["body"],
        message_id=message_id,
        in_reply_to=content["in_reply_to"],
    )
    receipt_hash = receipt_fingerprint(mime)
    if receipt_hash is None:
        raise OutlookError("This reply could not be prepared for sending", 422)
    # The conditional update is an independent execution fence even on stores
    # without SELECT FOR UPDATE; a losing claim must not call Microsoft.
    claimed = await database.scalar(
        update(OutlookMailDraft)
        .where(
            OutlookMailDraft.id == draft.id,
            OutlookMailDraft.status == "review",
            OutlookMailDraft.revision == payload.expected_revision,
            OutlookMailDraft.payload_hash == payload.expected_payload_hash,
        )
        .values(
            status="executing",
            send_request_id=payload.request_id,
            internet_message_id=message_id,
            receipt_hash=receipt_hash,
        )
        .returning(OutlookMailDraft.id)
    )
    if claimed is None:
        await database.rollback()
        return draft_view(settings, await owned_draft(database, scope, draft_id))
    await database.refresh(draft)
    audit(database, scope, "send_claimed", draft.id)
    await database.commit()  # Durable claim precedes the only provider send.
    try:
        await recheck(database, scope, connection_id, generation, send=True)
        draft = await owned_draft(database, scope, draft_id)
        if draft.status != "executing" or draft.send_request_id != payload.request_id:
            return draft_view(settings, draft)
        # Keep the owner/connection lock through transmission. A concurrent
        # disconnect winning before this point stops the send; later disconnects wait.
        await MicrosoftMailGateway().send(token, mime)
        draft.status = "uncertain"
        await database.commit()
    except (MicrosoftMailError, OutlookError, TimeoutError):
        await database.rollback()
        # The durable claim remains even on transport timeout or revoked sessions.
        # It is never reset to review, so an ambiguous request cannot duplicate mail.
        await mark_uncertain(database, scope, draft_id)
        raise OutlookError(
            ("The send outcome is unconfirmed. Check Outlook Sent before taking another action"),
            409,
        ) from None
    return await check(database, settings, scope, draft_id)


async def mark_uncertain(database: AsyncSession, scope: Scope, draft_id: UUID) -> None:
    # Content-free receipt-state repair is safe even if the browser session expired
    # during the network request. It grants no access and cannot trigger mail.
    await database.execute(
        update(OutlookMailDraft)
        .where(
            OutlookMailDraft.id == draft_id,
            OutlookMailDraft.user_id == scope.user_id,
            OutlookMailDraft.workspace_id == scope.workspace_id,
            OutlookMailDraft.status == "executing",
        )
        .values(status="uncertain")
    )
    await database.commit()


async def check(
    database: AsyncSession, settings: Settings, scope: Scope, draft_id: UUID
) -> dict[str, object]:
    draft = await owned_draft(database, scope, draft_id)
    if draft.status in {"completed", "review"}:
        return draft_view(settings, draft)
    if not draft.receipt_hash:
        raise OutlookError(
            "This send record cannot be checked automatically. Inspect Outlook Sent", 409
        )
    expected_hash = draft.receipt_hash
    current = (
        await database.scalars(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.user_id == scope.user_id,
                ConnectorConnection.workspace_id == scope.workspace_id,
                ConnectorConnection.provider == "microsoft",
                ConnectorConnection.status.in_(("CONNECTED", "DEGRADED")),
            )
            .order_by(ConnectorConnection.created_at.desc())
        )
    ).all()
    target = next((row for row in current if mailbox_binding(row) == draft.mailbox_binding), None)
    if target is None:
        raise OutlookError("Reconnect the same Microsoft mailbox to check this send outcome", 409)
    connection_id, message_id = target.id, draft.internet_message_id
    await database.commit()
    try:
        token, generation = await access(database, settings, scope, connection_id)
        receipt = await MicrosoftMailGateway().sent_receipt(token, message_id or "", expected_hash)
        await recheck(database, scope, connection_id, generation)
    except MicrosoftMailError:
        await database.rollback()
        draft = await owned_draft(database, scope, draft_id)
        if draft.status == "executing":
            draft.status = "uncertain"
        await database.commit()
        return draft_view(settings, draft, connection_id=connection_id)
    draft = await owned_draft(database, scope, draft_id)
    if receipt is not None:
        draft.sent_message_id = receipt
        draft.verified_at = now()
        draft.status = "completed"
        audit(database, scope, "send_verified", draft.id)
    elif draft.status != "completed":
        draft.status = "uncertain"
    await database.commit()
    return draft_view(settings, draft, connection_id=connection_id)


async def generate(
    database: AsyncSession, settings: Settings, scope: Scope, payload: Generate
) -> dict[str, object]:
    row, message, generation = await read_message(
        database, settings, scope, payload.connection_id, payload.source_message_id
    )
    if message.recipient.casefold() == mailbox(row).casefold():
        raise OutlookError("An email you sent cannot authorize a reply to yourself", 422)
    resource_id = stable_resource_id(row.id, "communication.message", message.id)
    document = SourceDocument(
        id=resource_id,
        workspace_id=scope.workspace_id,
        provider="microsoft",
        source_type="outlook_message",
        external_id=message.id,
        external_parent_id=message.conversation_id or None,
        author=SourceIdentity(
            provider="microsoft", identity_type="email", identity_value=message.recipient
        ),
        recipients=[
            SourceIdentity(provider="microsoft", identity_type="email", identity_value=address)
            for address in message.to
        ],
        subject=message.subject,
        content=message.body[:24000],
        occurred_at=message.sent_at,
        retrieved_at=now(),
    )
    resource = CanonicalResource(
        resource_id=resource_id,
        workspace_id=scope.workspace_id,
        connector_connection_id=row.id,
        provider="microsoft",
        resource_type="communication.message",
        external_id=message.id,
        external_parent_id=message.conversation_id or None,
        canonical={"status": "active"},
        provider_metadata={"source_hash": source_document_hash(document)},
        retrieved_at=document.retrieved_at,
    )
    runtime = await build_runtime(settings)
    task = source_task(
        workspace_id=scope.workspace_id,
        user_id=scope.user_id,
        connector=row,
        source_id=resource_id,
        policy=runtime.store.operator_policy,
        profile=Profile.ASSISTANT_INTERACTIVE,
        task_type=TaskType.DRAFT_COMMUNICATION,
        prompt="communication_draft",
        latency=LatencyClass.INTERACTIVE,
        max_output_tokens=2000,
    )
    request: dict[str, object] = {
        "instructions": payload.instructions,
        "calendar_rule": (
            "Calendar availability has not been checked by this operation. Do not "
            "claim a time is free unless the user's instructions include checked "
            "options."
        ),
    }
    if payload.previous:
        request["previous_draft"] = payload.previous.model_dump()
        request["edit_rule"] = (
            "Preserve the user's edits unless their new instructions request a change."
        )
    await database.commit()

    def validate(value: Any) -> None:
        generated_draft(value, recipient=message.recipient)

    result = await runtime.execute(
        task,
        context_builder=ContextBuilder(
            database, secrets=configured_secrets(settings), fetched_sources=(resource,)
        ),
        documents={resource_id: document},
        semantic_validator=validate,
        user_request=json.dumps(request),
    )
    await recheck(database, scope, payload.connection_id, generation)
    content = generated_draft(json.loads(result.output.text), recipient=message.recipient)
    return {"to": message.recipient, "subject": content.subject, "body": content.body}
