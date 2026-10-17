"""RFC 8291 encryption and RFC 8292 VAPID over pinned, approved HTTPS origins."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid  # type: ignore[import-untyped]
from pywebpush import WebPusher  # type: ignore[import-untyped]

from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.import_storage import cipher
from navox.connectors.outbound import ApprovedHTTPSTransport
from navox.core.settings import Settings
from navox.student_alerts.schemas import SubscriptionInput, decode_key, endpoint_origin


def configured(settings: Settings) -> bool:
    if not settings.student_push_enabled or settings.student_push_vapid_private_key is None:
        return False
    try:
        private_bytes = decode_key(settings.student_push_vapid_private_key.get_secret_value(), 32)
        private = ec.derive_private_key(int.from_bytes(private_bytes, "big"), ec.SECP256R1())
        public = private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        if public != decode_key(settings.student_push_vapid_public_key, 65):
            return False
        subject = urlsplit(settings.student_push_vapid_subject)
        if (
            subject.scheme not in {"mailto", "https"}
            or (subject.scheme == "mailto" and not subject.path)
            or (subject.scheme == "https" and not subject.hostname)
            or bool(subject.username or subject.password)
            or subject.fragment
            or any(ord(char) < 33 for char in settings.student_push_vapid_subject)
        ):
            return False
        cipher(settings)
    except (ValueError, TypeError, ConnectorRuntimeError):
        return False
    return True


async def send_push(
    subscription: SubscriptionInput,
    payload: dict[str, str],
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    if not configured(settings) or settings.student_push_vapid_private_key is None:
        raise ValueError("Class notifications are not configured")
    origin = endpoint_origin(subscription.endpoint)
    # Only the encryption API is used: pywebpush's network client never receives
    # an endpoint. All outbound I/O shares NavoX's DNS-pinned, no-proxy transport.
    encoded = WebPusher(subscription.model_dump()).encode(
        json.dumps(payload, separators=(",", ":")).encode(), "aes128gcm"
    )
    body = cast(bytes, encoded["body"])
    vapid = Vapid.from_string(settings.student_push_vapid_private_key.get_secret_value())
    headers = cast(
        dict[str, str],
        vapid.sign(
            {
                "aud": origin,
                "sub": settings.student_push_vapid_subject,
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            }
        ),
    )
    headers.update(
        {
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": "120",
            "Urgency": "normal",
            "Topic": payload["tag"][:32],
        }
    )
    path = urlsplit(subscription.endpoint).path
    active_transport = transport or ApprovedHTTPSTransport(
        origin, allowed_post_paths=frozenset({path})
    )
    # Calling the transport directly avoids httpx's INFO log of the complete
    # request URL: a push endpoint's path is a secret capability.
    async with active_transport:
        async with asyncio.timeout(15):
            response = await active_transport.handle_async_request(
                httpx.Request("POST", subscription.endpoint, content=body, headers=headers)
            )
            try:
                return response.status_code
            finally:
                await response.aclose()
