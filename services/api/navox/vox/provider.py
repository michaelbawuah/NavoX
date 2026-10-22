"""Server-only LiveKit Cloud transport with bounded, content-free control calls.

LiveKit Cloud token revocation is required: room deletion and JWT expiry do not
revoke active/refreshed participant tokens. No HTTP request may select the host,
identities, grants, agent name or revocation cutoff.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from navox.core.settings import Settings
from navox.vox.media import ConnectionIntent
from navox.vox.reconcile import CleanupClaim, CleanupReceipt

MAX_RESPONSE_BYTES = 65536
CONTROL_SECONDS = 8
_LOCATOR = re.compile(r"^(vx|vc|va)-[a-f0-9]{32}$")
_AGENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class MediaUnavailable(Exception):
    """Sanitized failure; never attach provider bodies or request headers."""


@dataclass(frozen=True)
class LiveKitCloudConfig:
    server_url: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    agent_name: str = "navox-vox"

    @property
    def http_url(self) -> str:
        return "https://" + urlsplit(self.server_url).netloc


def cloud_config(settings: Settings, *, require_release: bool = True) -> LiveKitCloudConfig:
    """Only an explicitly reviewed Cloud deployment can be a revocation authority."""
    if not settings.student_vox_livekit_cloud_qualified or (
        require_release
        and not (
            settings.student_vox_enabled
            and settings.student_vox_media_enabled
            and settings.student_vox_worker_qualified
        )
    ):
        raise MediaUnavailable("Vox media is not available")
    value = settings.student_vox_livekit_url
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise MediaUnavailable("Vox media is not available") from None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "wss"
        or not re.fullmatch(r"[a-z0-9-]+\.livekit\.cloud", host)
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not _AGENT_NAME.fullmatch(settings.student_vox_livekit_agent_name)
    ):
        raise MediaUnavailable("Vox media is not available")
    key, secret = settings.student_vox_livekit_api_key, settings.student_vox_livekit_api_secret
    if key is None or secret is None:
        raise MediaUnavailable("Vox media is not available")
    key_value, secret_value = key.get_secret_value(), secret.get_secret_value()
    if not key_value.strip() or len(secret_value) < 32:
        raise MediaUnavailable("Vox media is not available")
    return LiveKitCloudConfig(
        "wss://" + host, key_value, secret_value, settings.student_vox_livekit_agent_name
    )


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _locators(room: str, client: str, agent: str) -> None:
    if (
        not all(_LOCATOR.fullmatch(value) for value in (room, client, agent))
        or not room.startswith("vx-")
        or not client.startswith("vc-")
        or not agent.startswith("va-")
        or len({room, client, agent}) != 3
    ):
        raise MediaUnavailable("The Vox connection is invalid")


class LiveKitCloudProvider:
    def __init__(
        self,
        config: LiveKitCloudConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    def _jwt(self, claims: dict[str, Any]) -> str:
        payload = {"iss": self.config.api_key, **claims}
        message = (
            _encoded(b'{"alg":"HS256","typ":"JWT"}')
            + "."
            + _encoded(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        )
        signature = hmac.new(self.config.api_secret.encode(), message.encode(), hashlib.sha256)
        return message + "." + _encoded(signature.digest())

    def participant_token(
        self, intent: ConnectionIntent, *, role: Literal["client", "agent"] = "client"
    ) -> str:
        _locators(intent.room_name, intent.client_identity, intent.agent_identity)
        now = datetime.now(UTC)
        if (
            role not in {"client", "agent"}
            or intent.status != "confirmed"
            or intent.created_at.tzinfo is None
            or intent.expires_at.tzinfo is None
            or not 0 < (intent.expires_at - now).total_seconds() <= 120
            or not 0 < (intent.expires_at - intent.created_at).total_seconds() <= 120
        ):
            raise MediaUnavailable("The Vox connection has ended")
        return self._jwt(
            {
                "sub": intent.client_identity if role == "client" else intent.agent_identity,
                "nbf": math.floor(min(intent.created_at, now).timestamp()) - 1,
                "exp": math.floor(intent.expires_at.timestamp()),
                "metadata": json.dumps(
                    {"connection_id": str(intent.connection_id), "generation": intent.generation},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "video": {
                    "room": intent.room_name,
                    "roomJoin": True,
                    "canPublish": True,
                    "canPublishSources": ["microphone"],
                    "canSubscribe": True,
                    "canPublishData": False,
                    "canUpdateOwnMetadata": False,
                },
            }
        )

    async def _call(
        self,
        client: httpx.AsyncClient,
        service: str,
        method: str,
        payload: dict[str, Any],
        grants: dict[str, Any],
        *,
        missing_ok: bool = False,
    ) -> tuple[dict[str, Any], datetime]:
        now = datetime.now(UTC)
        token = self._jwt(
            {"nbf": int(now.timestamp()) - 5, "exp": int(now.timestamp()) + 30, "video": grants}
        )
        try:
            async with client.stream(
                "POST",
                self.config.http_url + f"/twirp/livekit.{service}/{method}",
                json=payload,
                headers={"Authorization": "Bearer " + token},
            ) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise MediaUnavailable("Vox media is temporarily unavailable")
                    raw.extend(chunk)
                try:
                    server_now = parsedate_to_datetime(response.headers.get("date", ""))
                    if server_now.tzinfo is None:
                        raise ValueError
                    server_now = server_now.astimezone(UTC)
                except (ValueError, TypeError, OverflowError):
                    raise MediaUnavailable("Vox media is temporarily unavailable") from None
                # Explicit cutoff semantics require a trustworthy provider clock.
                if abs((server_now - datetime.now(UTC)).total_seconds()) > 10:
                    raise MediaUnavailable("Vox media is temporarily unavailable")
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    raise MediaUnavailable("Vox media is temporarily unavailable") from None
                if not isinstance(data, dict):
                    raise MediaUnavailable("Vox media is temporarily unavailable")
                if response.status_code != 200:
                    if (
                        missing_ok
                        and response.status_code == 404
                        and data.get("code") == "not_found"
                    ):
                        return {}, server_now
                    raise MediaUnavailable("Vox media is temporarily unavailable")
                return data, server_now
        except (httpx.HTTPError, TimeoutError):
            raise MediaUnavailable("Vox media is temporarily unavailable") from None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(2.0),
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    async def provision(self, intent: ConnectionIntent) -> None:
        _locators(intent.room_name, intent.client_identity, intent.agent_identity)
        if intent.status not in {"preparing", "confirmed"} or intent.expires_at <= datetime.now(
            UTC
        ):
            raise MediaUnavailable("The Vox connection has ended")
        try:
            async with asyncio.timeout(CONTROL_SECONDS), self._client() as client:
                room, _ = await self._call(
                    client,
                    "RoomService",
                    "CreateRoom",
                    {
                        "name": intent.room_name,
                        "empty_timeout": 120,
                        "departure_timeout": 1,
                        "max_participants": 2,
                    },
                    {"room": intent.room_name, "roomCreate": True},
                )
                if (
                    room.get("name") != intent.room_name
                    or room.get("active_recording", False) is not False
                    or room.get("activeRecording", False) is not False
                ):
                    raise MediaUnavailable("Vox media is temporarily unavailable")
                metadata = json.dumps(
                    {
                        "connection_id": str(intent.connection_id),
                        "room_name": intent.room_name,
                        "client_identity": intent.client_identity,
                        "agent_identity": intent.agent_identity,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
                dispatch, _ = await self._call(
                    client,
                    "AgentDispatchService",
                    "CreateDispatch",
                    {
                        "agent_name": self.config.agent_name,
                        "room": intent.room_name,
                        "metadata": metadata,
                        "restart_policy": "JRP_NEVER",
                    },
                    {"room": intent.room_name, "roomAdmin": True},
                )
                if (
                    dispatch.get("room") != intent.room_name
                    or dispatch.get("agent_name", dispatch.get("agentName"))
                    != self.config.agent_name
                    or dispatch.get("metadata") != metadata
                    or not isinstance(dispatch.get("id"), str)
                    or not dispatch["id"]
                ):
                    raise MediaUnavailable("Vox media is temporarily unavailable")
        except TimeoutError:
            raise MediaUnavailable("Vox media is temporarily unavailable") from None

    async def revoke(self, claim: CleanupClaim) -> CleanupReceipt:
        _locators(claim.room_name, claim.client_identity, claim.agent_identity)
        try:
            async with asyncio.timeout(CONTROL_SECONDS), self._client() as client:
                _, server_now = await self._call(
                    client,
                    "RoomService",
                    "ListRooms",
                    {"names": [claim.room_name]},
                    {"roomList": True},
                )
                # A future cutoff closes same-second nbf and refreshed-token races.
                # It remains well inside LiveKit Cloud's required 60-second window.
                cutoff = math.ceil((server_now + timedelta(seconds=30)).timestamp())
                for identity in (claim.client_identity, claim.agent_identity):
                    await self._call(
                        client,
                        "RoomService",
                        "RemoveParticipant",
                        {"room": claim.room_name, "identity": identity, "revoke_token_ts": cutoff},
                        {"room": claim.room_name, "roomAdmin": True},
                    )
                await self._call(
                    client,
                    "RoomService",
                    "DeleteRoom",
                    {"room": claim.room_name},
                    {"room": claim.room_name, "roomCreate": True},
                    missing_ok=True,
                )
                remaining, _ = await self._call(
                    client,
                    "RoomService",
                    "ListRooms",
                    {"names": [claim.room_name]},
                    {"roomList": True},
                )
                rooms = remaining.get("rooms", [])
                if not isinstance(rooms, list) or rooms:
                    raise MediaUnavailable("Vox media is temporarily unavailable")
        except TimeoutError:
            raise MediaUnavailable("Vox media is temporarily unavailable") from None
        return CleanupReceipt(
            claim.connection_id,
            claim.claim_id,
            claim.room_name,
            claim.client_identity,
            claim.agent_identity,
            True,
            True,
            True,
        )
