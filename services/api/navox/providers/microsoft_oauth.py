"""Microsoft identity platform (v2.0) OAuth for Outlook / Microsoft 365 calendars.

Authorization code flow with PKCE. Calendar purposes request only what they
need: busy-time reads use `Calendars.Read`; reviewed scheduling adds
`Calendars.ReadWrite`. Mail is never requested here. Tokens are returned to the
caller for the encrypted credential vault and are never logged.

School accounts may require IT approval ("admin consent"). The provider
reports that as a distinct, student-explainable error instead of a generic one.
"""

import asyncio
import base64
import hashlib
import json
from typing import Any, Literal, cast
from urllib.parse import urlencode

import httpx

from navox.core.settings import Settings

AUTHORITY = "https://login.microsoftonline.com"
IDENTITY_SCOPES = ("openid", "email", "offline_access")
AVAILABILITY_SCOPES = ("https://graph.microsoft.com/Calendars.Read",)
SCHEDULING_SCOPES = ("https://graph.microsoft.com/Calendars.ReadWrite",)
MAX_RESPONSE_BYTES = 65_536
ADMIN_CONSENT_CODES = ("AADSTS65001", "AADSTS90094", "AADSTS90095")


class MicrosoftOAuthError(RuntimeError):
    """Safe, content-free OAuth failure."""

    def __init__(self, reason: Literal["failed", "admin_consent_required", "not_configured"]):
        super().__init__(reason)
        self.reason = reason


def configured(settings: Settings) -> bool:
    return bool(
        settings.microsoft_oauth_client_id
        and settings.microsoft_oauth_client_secret is not None
        and settings.microsoft_oauth_redirect_uri
    )


def _secret(settings: Settings) -> str:
    if not configured(settings) or settings.microsoft_oauth_client_secret is None:
        raise MicrosoftOAuthError("not_configured")
    return settings.microsoft_oauth_client_secret.get_secret_value()


def _endpoint(settings: Settings, name: Literal["authorize", "token"]) -> str:
    tenant = settings.microsoft_oauth_tenant or "common"
    return f"{AUTHORITY}/{tenant}/oauth2/v2.0/{name}"


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def scopes_for(*, scheduling: bool) -> tuple[str, ...]:
    return (*IDENTITY_SCOPES, *(SCHEDULING_SCOPES if scheduling else AVAILABILITY_SCOPES))


def authorization_url(
    state_value: str, code_verifier: str, settings: Settings, *, scheduling: bool = False
) -> str:
    _secret(settings)
    query = {
        "client_id": settings.microsoft_oauth_client_id,
        "code_challenge": pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
        "redirect_uri": settings.microsoft_oauth_redirect_uri,
        "response_mode": "query",
        "response_type": "code",
        "scope": " ".join(scopes_for(scheduling=scheduling)),
        "state": state_value,
    }
    return f"{_endpoint(settings, 'authorize')}?{urlencode(query)}"


def callback_error(error: str | None, description: str | None) -> MicrosoftOAuthError | None:
    """Classify an error returned to the redirect URI, without echoing its text."""
    if not error:
        return None
    text = description or ""
    if error == "consent_required" or any(code in text for code in ADMIN_CONSENT_CODES):
        return MicrosoftOAuthError("admin_consent_required")
    return MicrosoftOAuthError("failed")


async def _token_request(
    data: dict[str, str],
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    try:
        async with (
            asyncio.timeout(12),
            httpx.AsyncClient(
                timeout=10, trust_env=False, follow_redirects=False, transport=transport
            ) as client,
            client.stream("POST", _endpoint(settings, "token"), data=data) as response,
        ):
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise MicrosoftOAuthError("failed")
                raw.extend(chunk)
            status = response.status_code
    except (httpx.HTTPError, TimeoutError):
        raise MicrosoftOAuthError("failed") from None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise MicrosoftOAuthError("failed") from None
    if not isinstance(value, dict):
        raise MicrosoftOAuthError("failed")
    if status != 200:
        description = value.get("error_description")
        raise callback_error(
            str(value.get("error") or "failed"),
            description if isinstance(description, str) else None,
        ) or MicrosoftOAuthError("failed")
    token = value.get("access_token")
    if not isinstance(token, str) or not token or value.get("token_type") != "Bearer":
        raise MicrosoftOAuthError("failed")
    return cast(dict[str, Any], value)


async def exchange_authorization_code(
    code: str,
    code_verifier: str,
    settings: Settings,
    *,
    scheduling: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    return await _token_request(
        {
            "client_id": settings.microsoft_oauth_client_id,
            "client_secret": _secret(settings),
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": settings.microsoft_oauth_redirect_uri,
            "scope": " ".join(scopes_for(scheduling=scheduling)),
        },
        settings,
        transport=transport,
    )


async def refresh_access_token(
    refresh_token: str,
    settings: Settings,
    *,
    scheduling: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    if not refresh_token:
        raise MicrosoftOAuthError("failed")
    return await _token_request(
        {
            "client_id": settings.microsoft_oauth_client_id,
            "client_secret": _secret(settings),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(scopes_for(scheduling=scheduling)),
        },
        settings,
        transport=transport,
    )


def granted(token_response: dict[str, Any], *, scheduling: bool) -> bool:
    """True only when the returned scope string includes the purpose's calendar scope."""
    value = token_response.get("scope")
    if not isinstance(value, str):
        return False
    returned = {item.rsplit("/", 1)[-1].casefold() for item in value.split()}
    needed = SCHEDULING_SCOPES if scheduling else AVAILABILITY_SCOPES
    return all(item.rsplit("/", 1)[-1].casefold() in returned for item in needed)
