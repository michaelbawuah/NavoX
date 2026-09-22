"""Authenticated, tenant-safe provider event ingestion endpoints."""

import asyncio
import base64
import binascii
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from navox.api.auth import DatabaseSession, SettingsDependency
from navox.core.settings import Settings, get_settings
from navox.db.models import Connection, IncomingEvent, ProviderEventSubscription
from navox.events.processor import IncomingEventProcessor, NormalizedProviderEvent
from navox.intelligence.dispatcher import dispatch_source
from navox.intelligence.jobs import SourceWork

router = APIRouter(prefix="/events", tags=["events"])

MAX_EVENT_BODY_BYTES = 64 * 1024
GOOGLE_PROVIDER = "google"


class EventAuthenticationError(RuntimeError):
    """Raised when a sender cannot prove it owns an event delivery."""


class EventDeliveryConfigurationError(RuntimeError):
    """Raised when a production-only event receiver is not configured."""


@dataclass(frozen=True)
class GoogleChannelCredentials:
    """One-time credentials a watch-creation worker sends only to Google."""

    channel_id: str
    channel_token: str


def hash_value(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def create_google_channel_subscription(
    connection: Connection,
    source: str,
    resource_id: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[ProviderEventSubscription, GoogleChannelCredentials]:
    """Create a durable subscription record and its secret delivery token.

    This helper is intentionally server-only. A later watch-renewal worker will
    pass the channel ID and token to Google; neither value is exposed to browsers.
    """

    if source not in {"calendar", "drive"}:
        raise ValueError("Google channel subscriptions support calendar or drive sources")
    channel_id = str(uuid4())
    channel_token = token_urlsafe(32)
    return (
        ProviderEventSubscription(
            connection_id=connection.id,
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            provider=GOOGLE_PROVIDER,
            source=source,
            channel_id=channel_id,
            channel_token_hash=hash_value(channel_token),
            resource_id=resource_id,
            expires_at=expires_at,
            status="active",
        ),
        GoogleChannelCredentials(channel_id=channel_id, channel_token=channel_token),
    )


async def bounded_request_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_EVENT_BODY_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Event payload is too large",
                )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid event content length",
            ) from error
    body = await request.body()
    if len(body) > MAX_EVENT_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Event payload is too large",
        )
    return body


def required_google_channel_headers(request: Request) -> tuple[str, str, str, str]:
    channel_id = request.headers.get("x-goog-channel-id")
    channel_token = request.headers.get("x-goog-channel-token")
    resource_id = request.headers.get("x-goog-resource-id")
    message_number = request.headers.get("x-goog-message-number")
    if (
        channel_id is None
        or not channel_id
        or channel_token is None
        or not channel_token
        or resource_id is None
        or not resource_id
        or message_number is None
        or not message_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required Google notification headers",
        )
    return channel_id, channel_token, resource_id, message_number


async def authenticated_google_channel_subscription(
    request: Request,
    database: DatabaseSession,
    source: str,
) -> tuple[ProviderEventSubscription, Connection, str, str]:
    channel_id, channel_token, resource_id, message_number = required_google_channel_headers(
        request
    )
    subscription = await database.scalar(
        select(ProviderEventSubscription).where(
            ProviderEventSubscription.channel_id == channel_id,
            ProviderEventSubscription.provider == GOOGLE_PROVIDER,
            ProviderEventSubscription.source == source,
            ProviderEventSubscription.status == "active",
        )
    )
    if subscription is None or not hmac.compare_digest(
        subscription.channel_token_hash, hash_value(channel_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google notification channel",
        )
    if subscription.resource_id is not None and not hmac.compare_digest(
        subscription.resource_id, resource_id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google notification resource",
        )
    connection = await database.get(Connection, subscription.connection_id)
    if (
        connection is None
        or connection.provider != GOOGLE_PROVIDER
        or connection.status != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive Google notification connection",
        )
    return subscription, connection, resource_id, message_number


def notification_state(request: Request) -> str:
    resource_state = request.headers.get("x-goog-resource-state")
    if resource_state is None or not resource_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Google resource state",
        )
    return resource_state


async def record_normalized_event(
    database: DatabaseSession,
    normalized_event: NormalizedProviderEvent,
) -> str:
    existing = await database.scalar(
        select(IncomingEvent).where(
            IncomingEvent.connection_id == normalized_event.connection_id,
            IncomingEvent.provider == normalized_event.provider,
            IncomingEvent.external_event_id == normalized_event.external_event_id,
        )
    )
    if existing is not None:
        return "duplicate"

    event = IncomingEvent(
        connection_id=normalized_event.connection_id,
        user_id=normalized_event.user_id,
        workspace_id=normalized_event.workspace_id,
        provider=normalized_event.provider,
        source=normalized_event.source,
        event_type=normalized_event.event_type,
        external_event_id=normalized_event.external_event_id,
        external_resource_id=normalized_event.external_resource_id,
        payload_hash=normalized_event.payload_hash,
        event_metadata=dict(normalized_event.metadata),
        occurred_at=normalized_event.occurred_at,
        status="received",
    )
    database.add(event)
    try:
        await database.flush()
    except IntegrityError:
        await database.rollback()
        existing = await database.scalar(
            select(IncomingEvent).where(
                IncomingEvent.connection_id == normalized_event.connection_id,
                IncomingEvent.provider == normalized_event.provider,
                IncomingEvent.external_event_id == normalized_event.external_event_id,
            )
        )
        if existing is not None:
            return "duplicate"
        raise

    IncomingEventProcessor().process(event)
    await database.commit()
    settings = get_settings()
    if event.source in {"gmail", "calendar"} and settings.ai_provider != "disabled":
        try:
            await dispatch_source(
                SourceWork(
                    str(event.connection_id),
                    str(event.user_id),
                    str(event.workspace_id),
                    event.source,
                    str(event.id),
                ),
                settings=settings,
                request_id=str(event.id),
            )
        except Exception:
            # The persisted pending row is an outbox; reconciliation retries dispatch.
            pass
    return "processed"


async def ingest_google_channel_notification(
    request: Request,
    database: DatabaseSession,
    source: str,
) -> dict[str, str]:
    body = await bounded_request_body(request)
    if body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google channel notifications must not contain a request body",
        )
    (
        subscription,
        connection,
        resource_id,
        message_number,
    ) = await authenticated_google_channel_subscription(request, database, source)
    resource_state = notification_state(request)
    external_event_id = f"{subscription.channel_id}:{message_number}"
    result = await record_normalized_event(
        database,
        NormalizedProviderEvent(
            connection_id=connection.id,
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            provider=GOOGLE_PROVIDER,
            source=source,
            event_type=f"google.{source}.notification",
            external_event_id=external_event_id,
            external_resource_id=resource_id,
            payload_hash=hash_value(
                "\x00".join(
                    (source, subscription.channel_id, resource_id, message_number, resource_state)
                )
            ),
            metadata={"resource_state": resource_state, "message_number": message_number},
        ),
    )
    return {"status": result}


def google_gmail_push_configuration(settings: Settings) -> tuple[str, str, str, str]:
    token_secret = settings.google_gmail_push_verification_token
    token = token_secret.get_secret_value() if token_secret is not None else ""
    subscription = settings.google_gmail_push_subscription.strip()
    audience = settings.google_pubsub_push_audience.strip()
    service_account = settings.google_pubsub_push_service_account.strip().casefold()
    if not all((subscription, audience, service_account, token)):
        raise EventDeliveryConfigurationError("Google Gmail push delivery is not configured")
    return subscription, audience, service_account, token


async def verify_google_pubsub_push(authorization_header: str | None, settings: Settings) -> None:
    _, audience, expected_service_account, _ = google_gmail_push_configuration(settings)
    if authorization_header is None or not authorization_header.startswith("Bearer "):
        raise EventAuthenticationError("Missing Google Pub/Sub authorization")
    token = authorization_header.removeprefix("Bearer ").strip()
    if not token:
        raise EventAuthenticationError("Missing Google Pub/Sub authorization")
    try:
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            token,
            GoogleAuthRequest(),
            audience,
        )
    except ValueError as error:
        raise EventAuthenticationError("Invalid Google Pub/Sub authorization") from error
    email = claims.get("email")
    email_verified = claims.get("email_verified")
    if (
        not isinstance(email, str)
        or not hmac.compare_digest(email.casefold(), expected_service_account)
        or email_verified not in (True, "true")
    ):
        raise EventAuthenticationError("Unexpected Google Pub/Sub identity")


def parse_google_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Google Pub/Sub publish time",
        ) from error
    if timestamp.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google Pub/Sub publish time must include a timezone",
        )
    return timestamp.astimezone(UTC)


@dataclass(frozen=True)
class GmailNotification:
    message_id: str
    email_address: str
    history_id: str
    occurred_at: datetime | None


def parse_gmail_notification(body: bytes, expected_subscription: str) -> GmailNotification:
    try:
        envelope = json.loads(body)
        if not isinstance(envelope, dict) or envelope.get("subscription") != expected_subscription:
            raise ValueError("unexpected subscription")
        message = envelope.get("message")
        if not isinstance(message, dict):
            raise ValueError("missing message")
        message_id = message.get("messageId")
        encoded_data = message.get("data")
        if not isinstance(message_id, str) or not message_id or not isinstance(encoded_data, str):
            raise ValueError("invalid message")
        padding = "=" * (-len(encoded_data) % 4)
        notification = json.loads(base64.urlsafe_b64decode(encoded_data + padding))
        if not isinstance(notification, dict):
            raise ValueError("invalid Gmail notification")
        email_address = notification.get("emailAddress")
        history_id = notification.get("historyId")
        if not isinstance(email_address, str) or not isinstance(history_id, str):
            raise ValueError("missing Gmail notification fields")
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Gmail Pub/Sub notification",
        ) from error
    return GmailNotification(
        message_id=message_id,
        email_address=email_address.strip().casefold(),
        history_id=history_id,
        occurred_at=parse_google_timestamp(message.get("publishTime")),
    )


@router.post("/calendar")
async def receive_google_calendar_notification(
    request: Request,
    database: DatabaseSession,
) -> dict[str, str]:
    return await ingest_google_channel_notification(request, database, "calendar")


@router.post("/drive")
async def receive_google_drive_notification(
    request: Request,
    database: DatabaseSession,
) -> dict[str, str]:
    return await ingest_google_channel_notification(request, database, "drive")


@router.post("/gmail")
async def receive_google_gmail_notification(
    request: Request,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    try:
        expected_subscription, _, _, verification_token = google_gmail_push_configuration(settings)
    except EventDeliveryConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Gmail event delivery is not configured",
        ) from error
    supplied_token = request.query_params.get("token", "")
    if not hmac.compare_digest(supplied_token, verification_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google Gmail verification token",
        )
    try:
        await verify_google_pubsub_push(request.headers.get("authorization"), settings)
    except EventDeliveryConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Gmail event delivery is not configured",
        ) from error
    except EventAuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google Pub/Sub authorization",
        ) from error

    notification = parse_gmail_notification(
        await bounded_request_body(request),
        expected_subscription,
    )
    connections = list(
        await database.scalars(
            select(Connection).where(
                Connection.provider == GOOGLE_PROVIDER,
                Connection.external_email == notification.email_address,
                Connection.status == "active",
            )
        )
    )
    if len(connections) != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Google Gmail notification does not resolve to one active connection",
        )
    connection = connections[0]
    result = await record_normalized_event(
        database,
        NormalizedProviderEvent(
            connection_id=connection.id,
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            provider=GOOGLE_PROVIDER,
            source="gmail",
            event_type="google.gmail.history_changed",
            external_event_id=f"pubsub:{notification.message_id}",
            external_resource_id=notification.history_id,
            payload_hash=hash_value(
                "\x00".join(
                    (
                        notification.message_id,
                        notification.email_address,
                        notification.history_id,
                    )
                )
            ),
            metadata={"history_id": notification.history_id},
            occurred_at=notification.occurred_at,
        ),
    )
    return {"status": result}
