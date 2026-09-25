"""Institution-approved Canvas OAuth. No personal-token collection endpoint."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import SecretStr

from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.errors import retry_after_seconds
from navox.connectors.outbound import ApprovedHTTPSTransport, approved_origin
from navox.core.settings import Settings

CANVAS_SCOPES = {
    "academic.courses.read": "url:GET|/api/v1/courses",
    "academic.assignments.read": "url:GET|/api/v1/courses/:course_id/assignments",
    # Submission state is requested only as the authorized current-user include.
    "academic.submissions.read": "url:GET|/api/v1/courses/:course_id/assignments",
    "academic.announcements.read": "url:GET|/api/v1/announcements",
    "calendar.events.read": "url:GET|/api/v1/calendar_events",
}


@dataclass(frozen=True)
class CanvasDeployment:
    origin: str
    client_id: str
    client_secret: SecretStr
    redirect_uri: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    self.origin,
                    self.client_id,
                    self.redirect_uri,
                    self.client_secret.get_secret_value(),
                ]
            ).encode()
        ).hexdigest()


def deployment(settings: Settings) -> CanvasDeployment:
    try:
        origin = approved_origin(settings.canvas_base_url)
        callback = urlsplit(settings.canvas_oauth_redirect_uri)
        if (
            not settings.canvas_oauth_client_id.isdigit()
            or settings.canvas_oauth_client_secret is None
            or not settings.canvas_oauth_client_secret.get_secret_value()
            or not callback.hostname
            or callback.username
            or callback.password
            or callback.query
            or callback.fragment
            or callback.path != "/api/v1/connectors/canvas-lms/callback"
            or (
                callback.scheme != "https"
                and not (
                    settings.app_environment in {"development", "test"}
                    and callback.scheme == "http"
                    and callback.hostname in {"localhost", "127.0.0.1"}
                )
            )
        ):
            raise ValueError
        return CanvasDeployment(
            origin,
            settings.canvas_oauth_client_id,
            settings.canvas_oauth_client_secret,
            settings.canvas_oauth_redirect_uri,
        )
    except ValueError:
        raise ConnectorRuntimeError("PERMANENT_FAILURE", "Canvas OAuth is not configured") from None


def validate_capabilities(values: list[str]) -> frozenset[str]:
    selected = frozenset(values)
    if (
        not selected
        or len(selected) != len(values)
        or not selected.issubset(CANVAS_SCOPES)
        or "academic.courses.read" not in selected
        or ("academic.submissions.read" in selected and "academic.assignments.read" not in selected)
    ):
        raise ValueError("Select supported read permissions and their required course access")
    return selected


def authorization_url(config: CanvasDeployment, state: str, capabilities: list[str]) -> str:
    selected = validate_capabilities(capabilities)
    return (
        config.origin
        + "/login/oauth2/auth?"
        + urlencode(
            {
                "client_id": config.client_id,
                "response_type": "code",
                "state": state,
                "redirect_uri": config.redirect_uri,
                "scope": " ".join(sorted({CANVAS_SCOPES[c] for c in selected})),
                "purpose": "NavoX read-only academic context",
            }
        )
    )


@dataclass(frozen=True)
class CanvasTokens:
    access: SecretStr
    refresh: SecretStr | None
    user_id: str


def safe_client(origin: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ApprovedHTTPSTransport(origin),
        trust_env=False,
        follow_redirects=False,
        timeout=30,
    )


async def exchange(
    config: CanvasDeployment, *, code: str | None = None, refresh: str | None = None
) -> CanvasTokens:
    data = {"client_id": config.client_id, "client_secret": config.client_secret.get_secret_value()}
    if code is not None:
        data.update(grant_type="authorization_code", code=code, redirect_uri=config.redirect_uri)
    elif refresh is not None:
        data.update(
            grant_type="refresh_token", refresh_token=refresh, redirect_uri=config.redirect_uri
        )
    else:
        raise ValueError("Authorization code or refresh token required")
    async with safe_client(config.origin) as client:
        response = await client.post(config.origin + "/login/oauth2/token", data=data)
    if response.status_code == 429:
        raise ConnectorRuntimeError(
            "RATE_LIMITED",
            "Canvas rate limit reached",
            retry_after_seconds=retry_after_seconds(response.headers.get("Retry-After")),
        )
    if response.status_code in {400, 401, 403}:
        raise ConnectorRuntimeError("AUTH_EXPIRED", "Canvas authorization must be renewed")
    if response.status_code != 200:
        raise ConnectorRuntimeError("PROVIDER_UNAVAILABLE", "Canvas authorization is unavailable")
    try:
        body = response.json()
        if not isinstance(body, dict) or str(body.get("token_type", "")).lower() != "bearer":
            raise ValueError
        access = body.get("access_token")
        token = body.get("refresh_token")
        user = body.get("user")
        if (
            not isinstance(access, str)
            or not 1 <= len(access) <= 16_384
            or any(c.isspace() for c in access)
        ):
            raise ValueError
        if code is not None and (not isinstance(token, str) or not 1 <= len(token) <= 16_384):
            raise ValueError
        if not isinstance(user, dict) or isinstance(user.get("id"), bool):
            raise ValueError
        uid = str(user.get("id", ""))
        if not uid.isdigit() or not 0 < len(uid) <= 32:
            raise ValueError
        return CanvasTokens(
            SecretStr(access), SecretStr(token) if isinstance(token, str) else None, uid
        )
    except (ValueError, TypeError):
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE", "Invalid Canvas authorization response"
        ) from None
