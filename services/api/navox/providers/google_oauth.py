from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.credential_vault import CredentialVault, CredentialVaultError
from navox.core.settings import Settings
from navox.db.models import Connection, ConnectionCredential

GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


class GoogleAccessTokenError(RuntimeError):
    """Raised when a protected Google access token cannot be obtained."""


def client_secret(settings: Settings) -> str:
    secret = settings.google_oauth_client_secret
    if not settings.google_oauth_client_id or secret is None or not secret.get_secret_value():
        raise GoogleAccessTokenError("Google OAuth is not configured")
    return secret.get_secret_value()


async def access_token_for_connection(
    database: AsyncSession,
    *,
    connection: Connection,
    settings: Settings,
) -> str:
    if connection.provider != "google" or connection.status != "active":
        raise GoogleAccessTokenError("Google connection is not active")
    if connection.credential_reference is None:
        raise GoogleAccessTokenError("Google credential is unavailable")
    credential = await database.get(ConnectionCredential, connection.credential_reference)
    if credential is None:
        raise GoogleAccessTokenError("Google credential is unavailable")
    try:
        refresh_token = CredentialVault(settings).reveal_refresh_token(
            credential.encrypted_refresh_token
        )
    except CredentialVaultError as error:
        raise GoogleAccessTokenError("Google credential could not be decrypted") from error

    payload = {
        "client_id": settings.google_oauth_client_id,
        "client_secret": client_secret(settings),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
            response.raise_for_status()
            response_data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise GoogleAccessTokenError("Google access token refresh failed") from error

    if not isinstance(response_data, dict):
        raise GoogleAccessTokenError("Google returned an invalid token response")
    data = cast(dict[str, Any], response_data)
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GoogleAccessTokenError("Google did not return an access token")
    scopes = data.get("scope")
    if isinstance(scopes, str):
        # Refresh responses may narrow grants; the old permission list is not authority.
        connection.granted_scopes = scopes.split()

    expires_in = data.get("expires_in")
    connection.access_token_expires_at = (
        datetime.now(UTC) + timedelta(seconds=int(expires_in))
        if isinstance(expires_in, int | str) and str(expires_in).isdigit()
        else None
    )
    connection.last_checked_at = datetime.now(UTC)
    connection.last_error = None
    return access_token
