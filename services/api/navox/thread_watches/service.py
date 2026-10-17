"""Fail-closed metadata checks bound to an unchanged verified send and opt-in."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.approvals.schemas import StoredGmailSendPayload
from navox.connectors.builtin.google_gmail import GMAIL_CAPABILITY
from navox.core.settings import Settings
from navox.db.models import (
    Action,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.thread_watches import ThreadWatch
from navox.intelligence.source_cooldown import source_retry_after
from navox.providers.google_oauth import GoogleAccessTokenError, access_token_for_connection
from navox.providers.google_sources import GMAIL_READ_SCOPE
from navox.providers.google_thread_watch import (
    GoogleThreadWatchGateway,
    ThreadWatchGateway,
    ThreadWatchMetadataError,
    classify_reply,
)

MONITORED = frozenset({"watching", "due", "needs_access", "check_failed"})
TERMINAL = frozenset({"paused", "cancelled", "replied"})
PROVIDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class WatchAccessError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SendBinding:
    connection_id: UUID
    sender: str
    recipient: str
    subject: str
    message_id: str
    thread_id: str
    payload_hash: str


def send_binding(action: Action) -> SendBinding:
    if (
        action.provider != "google"
        or action.action_type != "gmail.send"
        or action.status != "completed"
        or action.verified_at is None
    ):
        raise WatchAccessError("send_not_verified")
    try:
        payload = StoredGmailSendPayload.model_validate(action.payload)
    except ValidationError:
        raise WatchAccessError("send_reference_unavailable") from None
    message_id, thread_id = action.result.get("message_id"), action.result.get("thread_id")
    if (
        not isinstance(message_id, str)
        or not PROVIDER_ID.fullmatch(message_id)
        or not isinstance(thread_id, str)
        or not PROVIDER_ID.fullmatch(thread_id)
        or str(payload.sender).casefold() == str(payload.to).casefold()
    ):
        raise WatchAccessError("send_reference_unavailable")
    return SendBinding(
        payload.connection_id,
        str(payload.sender).casefold(),
        str(payload.to).casefold(),
        payload.subject,
        message_id,
        thread_id,
        action.payload_hash,
    )


@dataclass(frozen=True)
class Authority:
    connection_id: UUID
    user_id: UUID
    workspace_id: UUID
    credential_id: UUID
    credential_version: str
    external_account_id: str
    email: str
    scopes: frozenset[str]
    native_state: tuple[tuple[str, str], ...]
    owner_state: tuple[str, str, str]


async def authorize(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    connection_id: UUID,
    sender: str,
    lock: bool = False,
) -> tuple[Connection, Authority]:
    # The same owner/pause checks run before refresh, before Gmail and before publication.
    # Match connector lifecycle ordering: native rows, membership, user, then
    # legacy authority. Never hold a watch lock while acquiring these locks.
    native_query = (
        select(ConnectorConnection)
        .join(ConnectorDefinition)
        .where(
            ConnectorConnection.legacy_connection_id == connection_id,
            ConnectorDefinition.connector_key == "google-gmail",
        )
        .order_by(ConnectorConnection.id)
    )
    if lock:
        native_query = native_query.with_for_update(of=ConnectorConnection)
    native_rows = list(
        await database.scalars(native_query.execution_options(populate_existing=True))
    )
    user_query = select(User).where(User.id == user_id)
    workspace_query = select(Workspace).where(Workspace.id == workspace_id)
    member_query = select(WorkspaceMembership).where(
        WorkspaceMembership.user_id == user_id,
        WorkspaceMembership.workspace_id == workspace_id,
    )
    connection_query = select(Connection).where(
        Connection.id == connection_id,
        Connection.user_id == user_id,
        Connection.workspace_id == workspace_id,
    )
    if lock:
        user_query = user_query.with_for_update()
        member_query = member_query.with_for_update()
        connection_query = connection_query.with_for_update()
    member = await database.scalar(member_query.execution_options(populate_existing=True))
    user = await database.scalar(user_query.execution_options(populate_existing=True))
    native_state: list[tuple[str, str]] = []
    for native in native_rows:
        definition_query = select(ConnectorDefinition).where(
            ConnectorDefinition.id == native.connector_definition_id
        )
        definition = await database.scalar(
            definition_query.execution_options(populate_existing=True)
        )
        if (
            definition is None
            or native.user_id != user_id
            or native.workspace_id != workspace_id
            or not definition.active
            or native.status not in {"CONNECTED", "DEGRADED"}
            or native.health_state in {"PAUSED", "DISCONNECTED"}
            or GMAIL_CAPABILITY not in native.authorized_capabilities
        ):
            raise WatchAccessError("gmail_read_access_required")
        native_state.append((str(native.id), f"{native.updated_at}:{native.sync_generation}"))
    workspace = await database.scalar(workspace_query.execution_options(populate_existing=True))
    connection = await database.scalar(connection_query.execution_options(populate_existing=True))
    if user is None or workspace is None or member is None or member.role != "owner":
        raise WatchAccessError("owner_access_required")
    if workspace.workspace_type != "personal":
        raise WatchAccessError("personal_workspace_required")
    if user.agent_paused:
        raise WatchAccessError("navox_paused")
    if (
        connection is None
        or connection.provider != "google"
        or connection.status != "active"
        or connection.credential_reference is None
        or GMAIL_READ_SCOPE not in connection.granted_scopes
        or (connection.external_email or "").casefold() != sender
    ):
        raise WatchAccessError("gmail_read_access_required")
    credential = await database.get(
        ConnectionCredential, connection.credential_reference, populate_existing=True
    )
    if credential is None:
        raise WatchAccessError("gmail_read_access_required")
    authority = Authority(
        connection.id,
        user_id,
        workspace_id,
        connection.credential_reference,
        sha256(
            f"{credential.key_version}:{credential.encrypted_refresh_token}".encode()
        ).hexdigest(),
        connection.external_account_id,
        sender,
        frozenset(connection.granted_scopes),
        tuple(sorted(native_state)),
        (str(user.updated_at), str(member.created_at), str(workspace.updated_at)),
    )
    return connection, authority


async def owned_action(
    database: AsyncSession,
    *,
    action_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    lock: bool = False,
) -> Action:
    query = select(Action).where(
        Action.id == action_id, Action.user_id == user_id, Action.workspace_id == workspace_id
    )
    if lock:
        query = query.with_for_update()
    action = await database.scalar(query.execution_options(populate_existing=True))
    if action is None:
        raise WatchAccessError("action_not_found")
    return action


async def watch_authority(
    database: AsyncSession, watch: ThreadWatch, *, lock: bool = False
) -> tuple[Connection, Authority]:
    connection, authority = await authorize(
        database,
        user_id=watch.user_id,
        workspace_id=watch.workspace_id,
        connection_id=watch.connection_id,
        sender=watch.sender_email,
        lock=lock,
    )
    action = await owned_action(
        database,
        action_id=watch.action_id,
        user_id=watch.user_id,
        workspace_id=watch.workspace_id,
        lock=lock,
    )
    binding = send_binding(action)
    if binding != SendBinding(
        watch.connection_id,
        watch.sender_email,
        watch.recipient_email,
        watch.subject,
        watch.sent_message_id,
        watch.thread_id,
        watch.action_payload_hash,
    ):
        raise WatchAccessError("send_reference_changed")
    return connection, authority


async def _token(
    database: AsyncSession, watch: ThreadWatch, settings: Settings, authority: Authority
) -> str:
    original, current = await watch_authority(database, watch)
    if current != authority:
        raise WatchAccessError("access_changed")
    detached = Connection(
        id=original.id,
        user_id=original.user_id,
        workspace_id=original.workspace_id,
        provider=original.provider,
        status=original.status,
        external_account_id=original.external_account_id,
        external_email=original.external_email,
        credential_reference=original.credential_reference,
        granted_scopes=list(original.granted_scopes),
    )
    await database.commit()
    token = await access_token_for_connection(database, connection=detached, settings=settings)
    original, current = await authorize(
        database,
        user_id=authority.user_id,
        workspace_id=authority.workspace_id,
        connection_id=authority.connection_id,
        sender=authority.email,
        lock=True,
    )
    if current != authority:
        raise WatchAccessError("access_changed")
    original.granted_scopes = sorted(authority.scopes & set(detached.granted_scopes))
    original.access_token_expires_at = detached.access_token_expires_at
    original.last_checked_at = detached.last_checked_at
    await database.commit()
    if frozenset(detached.granted_scopes) != authority.scopes:
        raise WatchAccessError("gmail_read_access_required")
    return token


async def require_claim(
    database: AsyncSession, watch_id: UUID, revision: int, lease_token: UUID
) -> ThreadWatch:
    watch = await database.scalar(
        select(ThreadWatch)
        .where(ThreadWatch.id == watch_id)
        .execution_options(populate_existing=True)
    )
    if (
        watch is None
        or watch.revision != revision
        or watch.lease_token != lease_token
        or watch.status in TERMINAL
    ):
        raise WatchAccessError("watch_not_active")
    return watch


async def check_watch(
    database: AsyncSession,
    *,
    watch_id: UUID,
    revision: int,
    settings: Settings,
    gateway: ThreadWatchGateway | None = None,
    now: datetime | None = None,
) -> str:
    current_time = utc(now or datetime.now(UTC))
    watch = await database.scalar(
        select(ThreadWatch)
        .where(ThreadWatch.id == watch_id)
        .execution_options(populate_existing=True)
    )
    if watch is None or watch.revision != revision or watch.status not in MONITORED:
        return "superseded"
    if watch.lease_until is not None and utc(watch.lease_until) > current_time:
        return "checking"
    if watch.next_check_at is not None and utc(watch.next_check_at) > current_time:
        return "scheduled"
    token_id = uuid4()
    claimed = await database.scalar(
        update(ThreadWatch)
        .where(
            ThreadWatch.id == watch_id,
            ThreadWatch.revision == revision,
            ThreadWatch.status.in_(MONITORED),
            or_(ThreadWatch.lease_until.is_(None), ThreadWatch.lease_until <= current_time),
            or_(ThreadWatch.next_check_at.is_(None), ThreadWatch.next_check_at <= current_time),
        )
        .values(lease_token=token_id, lease_until=current_time + timedelta(minutes=2))
        .returning(ThreadWatch.id)
        .execution_options(synchronize_session=False)
    )
    await database.commit()
    if claimed is None:
        return "checking"
    outcome, reason, retry_seconds = "check_failed", "check_unavailable", 3600
    evidence = None
    checked_at = None
    provider_failure: ThreadWatchMetadataError | None = None
    try:
        watch = await require_claim(database, watch_id, revision, token_id)
        _, authority = await watch_authority(database, watch)
        cooldown = await source_retry_after(database, watch.connection_id, "gmail")
        if cooldown:
            retry_seconds = cooldown
            raise WatchAccessError("gmail_temporarily_unavailable")
        token = await _token(database, watch, settings, authority)
        watch = await require_claim(database, watch_id, revision, token_id)
        _, current = await watch_authority(database, watch)
        if current != authority:
            raise WatchAccessError("access_changed")
        cooldown = await source_retry_after(database, watch.connection_id, "gmail")
        if cooldown:
            retry_seconds = cooldown
            raise WatchAccessError("gmail_temporarily_unavailable")
        await database.commit()
        thread = await (gateway or GoogleThreadWatchGateway()).fetch(
            access_token=token, thread_id=watch.thread_id
        )
        _, current = await watch_authority(database, watch)
        if current != authority:
            raise WatchAccessError("access_changed")
        evidence = classify_reply(
            thread,
            sent_message_id=watch.sent_message_id,
            sender=watch.sender_email,
            recipient=watch.recipient_email,
        )
        checked_at = utc(now or datetime.now(UTC))
        outcome = (
            "replied" if evidence else ("due" if checked_at >= utc(watch.remind_at) else "watching")
        )
        reason = "reply_detected" if evidence else "no_reply_at_last_check"
    except WatchAccessError as error:
        reason = error.reason
        outcome = "check_failed" if reason == "gmail_temporarily_unavailable" else "needs_access"
    except GoogleAccessTokenError:
        outcome, reason = "needs_access", "gmail_read_access_required"
    except ThreadWatchMetadataError as error:
        provider_failure = error
        reason = error.code
        outcome = (
            "needs_access"
            if error.http_status in {401, 403} and error.code != "google_rate_limited"
            else "check_failed"
        )
        retry_seconds = max(60, min(86400, error.retry_after_seconds or 3600))
    except TimeoutError:
        reason = "check_timed_out"
    # Refresh the opt-in after I/O. A paused/cancelled/deleted or revised watch wins.
    await database.rollback()
    watch = await database.scalar(
        select(ThreadWatch)
        .where(ThreadWatch.id == watch_id)
        .execution_options(populate_existing=True)
    )
    if (
        watch is None
        or watch.revision != revision
        or watch.lease_token != token_id
        or watch.status in TERMINAL
    ):
        return "superseded"
    # The final ownership check happens in the same short transaction as publication.
    if checked_at is not None:
        try:
            _, current = await watch_authority(database, watch, lock=True)
            if current != authority:
                raise WatchAccessError("access_changed")
        except WatchAccessError as error:
            outcome, reason, evidence, checked_at = "needs_access", error.reason, None, None
    if outcome == "replied":
        next_check = None
    elif outcome == "due":
        # A later reply still resolves the reminder; no duplicate reminder is created.
        next_check = current_time + timedelta(hours=6)
    elif outcome == "watching":
        next_check = min(current_time + timedelta(hours=1), utc(watch.remind_at))
    else:
        next_check = current_time + timedelta(seconds=retry_seconds)
    published = await database.scalar(
        update(ThreadWatch)
        .where(
            ThreadWatch.id == watch_id,
            ThreadWatch.revision == revision,
            ThreadWatch.lease_token == token_id,
            ThreadWatch.status.not_in(TERMINAL),
        )
        .values(
            status=outcome,
            reason=reason,
            lease_token=None,
            lease_until=None,
            last_checked_at=checked_at if checked_at is not None else watch.last_checked_at,
            replied_at=evidence.received_at if evidence is not None else None,
            reply_message_id=evidence.message_id if evidence is not None else None,
            next_check_at=next_check,
        )
        .returning(ThreadWatch.id)
        .execution_options(synchronize_session=False)
    )
    if published is None:
        await database.rollback()
        return "superseded"
    if provider_failure is not None and provider_failure.code in {
        "google_rate_limited",
        "google_provider_unavailable",
    }:
        add_audit_event(
            database,
            user_id=watch.user_id,
            workspace_id=watch.workspace_id,
            event_type="intelligence.source.failed",
            entity_type="connection",
            entity_id=watch.connection_id,
            metadata={
                "source": "gmail",
                "error_diagnostic": {
                    "code": provider_failure.code,
                    "http_status": provider_failure.http_status,
                    "retry_after_seconds": retry_seconds,
                },
                "retry_not_before": (current_time + timedelta(seconds=retry_seconds)).isoformat(),
            },
        )
    await database.commit()
    return outcome


async def pending_watches(
    database: AsyncSession, *, now: datetime | None = None
) -> list[ThreadWatch]:
    current = utc(now or datetime.now(UTC))
    return list(
        await database.scalars(
            select(ThreadWatch)
            .where(
                ThreadWatch.status.in_(MONITORED),
                ThreadWatch.next_check_at <= current,
                or_(ThreadWatch.lease_until.is_(None), ThreadWatch.lease_until <= current),
            )
            .order_by(ThreadWatch.next_check_at, ThreadWatch.id)
            .limit(100)
        )
    )
