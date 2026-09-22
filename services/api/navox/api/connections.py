from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated, Any, cast
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.core.credential_vault import CredentialVault, CredentialVaultError
from navox.core.settings import Settings
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt

router = APIRouter(prefix="/connections", tags=["connections"])

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_IDENTITY_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
)
GOOGLE_GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GOOGLE_ACCOUNT_BINDING_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
)
GOOGLE_ALLOWED_SCOPES = frozenset((*GOOGLE_IDENTITY_SCOPES, GOOGLE_GMAIL_SEND_SCOPE))
OAUTH_ATTEMPT_TTL = timedelta(minutes=10)


class GoogleAuthorizationStartResponse(BaseModel):
    authorization_url: str
    requested_scopes: list[str]


class ConnectionResponse(BaseModel):
    id: UUID
    provider: str
    external_email: str | None
    status: str
    granted_scopes: list[str]
    last_checked_at: datetime | None
    last_error: str | None


class ConnectionHealthResponse(ConnectionResponse):
    healthy: bool


class GoogleOAuthProviderError(RuntimeError):
    """Raised when Google rejects or cannot complete an OAuth request."""


def hash_value(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(code_verifier: str) -> str:
    digest = sha256(code_verifier.encode("utf-8")).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def google_client_secret(settings: Settings) -> str:
    secret = settings.google_oauth_client_secret
    if not settings.google_oauth_client_id or secret is None or not secret.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured",
        )
    return secret.get_secret_value()


def google_vault(settings: Settings) -> CredentialVault:
    google_client_secret(settings)
    try:
        return CredentialVault(settings)
    except CredentialVaultError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google credential protection is not configured",
        ) from error


def authorization_url(
    state_value: str,
    code_verifier: str,
    settings: Settings,
    *,
    scopes: tuple[str, ...] = GOOGLE_IDENTITY_SCOPES,
    include_granted_scopes: bool = False,
) -> str:
    google_client_secret(settings)
    query_values = {
        "access_type": "offline",
        "client_id": settings.google_oauth_client_id,
        "code_challenge": pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
        "prompt": "consent",
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state_value,
    }
    if include_granted_scopes:
        query_values["include_granted_scopes"] = "true"
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(query_values)}"


async def exchange_authorization_code(
    code: str, code_verifier: str, settings: Settings
) -> dict[str, Any]:
    payload = {
        "client_id": settings.google_oauth_client_id,
        "client_secret": google_client_secret(settings),
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": settings.google_oauth_redirect_uri,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
            response.raise_for_status()
            response_data = response.json()
            if not isinstance(response_data, dict):
                raise GoogleOAuthProviderError("Google returned an invalid authorization response")
            return cast(dict[str, Any], response_data)
    except (httpx.HTTPError, ValueError) as error:
        raise GoogleOAuthProviderError("Google authorization could not be completed") from error


async def fetch_google_profile(access_token: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                GOOGLE_USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            response_data = response.json()
            if not isinstance(response_data, dict):
                raise GoogleOAuthProviderError("Google returned an invalid profile response")
            return cast(dict[str, Any], response_data)
    except (httpx.HTTPError, ValueError) as error:
        raise GoogleOAuthProviderError("Google profile verification failed") from error


async def refresh_google_access_token(
    refresh_token: str,
    settings: Settings,
) -> dict[str, Any]:
    payload = {
        "client_id": settings.google_oauth_client_id,
        "client_secret": google_client_secret(settings),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(GOOGLE_TOKEN_ENDPOINT, data=payload)
            response.raise_for_status()
            response_data = response.json()
            if not isinstance(response_data, dict):
                raise GoogleOAuthProviderError("Google returned an invalid health response")
            return cast(dict[str, Any], response_data)
    except (httpx.HTTPError, ValueError) as error:
        raise GoogleOAuthProviderError("Google connection health check failed") from error


def response_from_connection(connection: Connection) -> ConnectionResponse:
    return ConnectionResponse(
        id=connection.id,
        provider=connection.provider,
        external_email=connection.external_email,
        status=connection.status,
        granted_scopes=connection.granted_scopes,
        last_checked_at=connection.last_checked_at,
        last_error=connection.last_error,
    )


def connection_redirect(settings: Settings, outcome: str) -> RedirectResponse:
    url = f"{settings.web_origin.rstrip('/')}/?google_connection={outcome}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/google/start", response_model=GoogleAuthorizationStartResponse)
async def start_google_authorization(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> GoogleAuthorizationStartResponse:
    google_vault(settings)
    state_value = token_urlsafe(32)
    code_verifier = token_urlsafe(64)
    database.add(
        OAuthAuthorizationAttempt(
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            provider="google",
            purpose="identity",
            requested_scopes=list(GOOGLE_IDENTITY_SCOPES),
            state_hash=hash_value(state_value),
            code_verifier=code_verifier,
            expires_at=datetime.now(UTC) + OAUTH_ATTEMPT_TTL,
        )
    )
    await database.commit()
    return GoogleAuthorizationStartResponse(
        authorization_url=authorization_url(state_value, code_verifier, settings),
        requested_scopes=list(GOOGLE_IDENTITY_SCOPES),
    )


@router.get(
    "/google/{connection_id}/gmail-send/start",
    response_model=GoogleAuthorizationStartResponse,
)
async def start_google_gmail_send_authorization(
    connection_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> GoogleAuthorizationStartResponse:
    google_vault(settings)
    connection = await database.scalar(
        select(Connection).where(
            Connection.id == connection_id,
            Connection.user_id == current_account.user.id,
            Connection.workspace_id == current_account.workspace.id,
            Connection.provider == "google",
        )
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google connection not found",
        )
    if GOOGLE_GMAIL_SEND_SCOPE in connection.granted_scopes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gmail send permission is already granted",
        )

    state_value = token_urlsafe(32)
    code_verifier = token_urlsafe(64)
    database.add(
        OAuthAuthorizationAttempt(
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            provider="google",
            purpose="gmail_send",
            connection_id=connection.id,
            requested_scopes=[*GOOGLE_ACCOUNT_BINDING_SCOPES, GOOGLE_GMAIL_SEND_SCOPE],
            state_hash=hash_value(state_value),
            code_verifier=code_verifier,
            expires_at=datetime.now(UTC) + OAUTH_ATTEMPT_TTL,
        )
    )
    await database.commit()
    return GoogleAuthorizationStartResponse(
        authorization_url=authorization_url(
            state_value,
            code_verifier,
            settings,
            scopes=(*GOOGLE_ACCOUNT_BINDING_SCOPES, GOOGLE_GMAIL_SEND_SCOPE),
            include_granted_scopes=True,
        ),
        requested_scopes=[*GOOGLE_ACCOUNT_BINDING_SCOPES, GOOGLE_GMAIL_SEND_SCOPE],
    )


@router.get("/google/callback", include_in_schema=False)
async def complete_google_authorization(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    code: Annotated[str | None, Query()] = None,
    state_value: Annotated[str | None, Query(alias="state")] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    if error is not None:
        return connection_redirect(settings, "cancelled")
    if code is None or state_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Google callback",
        )

    attempt = await database.scalar(
        select(OAuthAuthorizationAttempt).where(
            OAuthAuthorizationAttempt.provider == "google",
            OAuthAuthorizationAttempt.state_hash == hash_value(state_value),
            OAuthAuthorizationAttempt.user_id == current_account.user.id,
            OAuthAuthorizationAttempt.workspace_id == current_account.workspace.id,
            OAuthAuthorizationAttempt.used_at.is_(None),
            OAuthAuthorizationAttempt.expires_at > datetime.now(UTC),
        )
    )
    if attempt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expired or invalid Google state",
        )

    attempt.used_at = datetime.now(UTC)
    await database.commit()

    try:
        token_response = await exchange_authorization_code(
            code,
            attempt.code_verifier,
            settings,
        )
        access_token = token_response.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GoogleOAuthProviderError("Google did not return an access token")
    except GoogleOAuthProviderError:
        return connection_redirect(settings, "failed")

    if attempt.purpose == "gmail_send":
        connection = await database.scalar(
            select(Connection).where(
                Connection.id == attempt.connection_id,
                Connection.user_id == current_account.user.id,
                Connection.workspace_id == current_account.workspace.id,
                Connection.provider == "google",
            )
        )
        if connection is None:
            return connection_redirect(settings, "failed")

        scopes_value = token_response.get("scope", "")
        returned_scopes = scopes_value.split() if isinstance(scopes_value, str) else []
        if GOOGLE_GMAIL_SEND_SCOPE not in returned_scopes:
            return connection_redirect(settings, "scope_mismatch")
        if not set(returned_scopes).issubset(GOOGLE_ALLOWED_SCOPES):
            return connection_redirect(settings, "scope_mismatch")
        if not set(GOOGLE_ACCOUNT_BINDING_SCOPES).issubset(returned_scopes):
            return connection_redirect(settings, "scope_mismatch")

        try:
            profile = await fetch_google_profile(access_token)
        except GoogleOAuthProviderError:
            return connection_redirect(settings, "failed")
        external_account_id = profile.get("sub")
        external_email = profile.get("email")
        email_verified = profile.get("email_verified")
        verified_email = email_verified is True or (
            isinstance(email_verified, str) and email_verified == "true"
        )
        if (
            external_account_id != connection.external_account_id
            or not isinstance(external_email, str)
            or external_email.strip().casefold() != connection.external_email
            or not verified_email
        ):
            return connection_redirect(settings, "account_mismatch")

        refresh_token = token_response.get("refresh_token")
        existing_credential = (
            await database.get(ConnectionCredential, connection.credential_reference)
            if connection.credential_reference is not None
            else None
        )
        if not isinstance(refresh_token, str) or not refresh_token:
            if existing_credential is None:
                return connection_redirect(settings, "refresh_token_required")

        expires_in = token_response.get("expires_in")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if isinstance(expires_in, int | str) and str(expires_in).isdigit()
            else None
        )
        vault = google_vault(settings)
        previous_credential_reference = connection.credential_reference
        if isinstance(refresh_token, str) and refresh_token:
            credential = ConnectionCredential(
                encrypted_refresh_token=vault.seal_refresh_token(refresh_token)
            )
            database.add(credential)
            await database.flush()
            connection.credential_reference = credential.id

        connection.granted_scopes = sorted(set(connection.granted_scopes) | set(returned_scopes))
        connection.status = "active"
        connection.access_token_expires_at = expires_at
        connection.last_checked_at = datetime.now(UTC)
        connection.last_error = None

        if (
            previous_credential_reference is not None
            and previous_credential_reference != connection.credential_reference
        ):
            previous_credential = await database.get(
                ConnectionCredential,
                previous_credential_reference,
            )
            if previous_credential is not None:
                await database.delete(previous_credential)

        await database.commit()
        return connection_redirect(settings, "gmail_send_enabled")

    try:
        profile = await fetch_google_profile(access_token)
    except GoogleOAuthProviderError:
        return connection_redirect(settings, "failed")

    external_account_id = profile.get("sub")
    external_email = profile.get("email")
    email_verified = profile.get("email_verified")
    verified_email = email_verified is True or (
        isinstance(email_verified, str) and email_verified == "true"
    )
    if (
        not isinstance(external_account_id, str)
        or not isinstance(external_email, str)
        or not external_email.strip()
        or not verified_email
    ):
        return connection_redirect(settings, "unverified")
    normalized_external_email = external_email.strip().casefold()

    refresh_token = token_response.get("refresh_token")
    scopes_value = token_response.get("scope", " ".join(GOOGLE_IDENTITY_SCOPES))
    granted_scopes = (
        scopes_value.split() if isinstance(scopes_value, str) else list(GOOGLE_IDENTITY_SCOPES)
    )
    if not set(granted_scopes).issubset(set(GOOGLE_IDENTITY_SCOPES)):
        return connection_redirect(settings, "scope_mismatch")
    expires_in = token_response.get("expires_in")
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=int(expires_in))
        if isinstance(expires_in, int | str) and str(expires_in).isdigit()
        else None
    )
    existing = await database.scalar(
        select(Connection).where(
            Connection.workspace_id == current_account.workspace.id,
            Connection.provider == "google",
            Connection.external_account_id == external_account_id,
        )
    )
    if not isinstance(refresh_token, str) or not refresh_token:
        if existing is None or existing.credential_reference is None:
            return connection_redirect(settings, "refresh_token_required")
    vault = google_vault(settings)
    await database.commit()

    async with database.begin():
        previous_credential_reference = (
            existing.credential_reference if existing is not None else None
        )
        credential_reference: UUID | None
        if isinstance(refresh_token, str) and refresh_token:
            credential = ConnectionCredential(
                encrypted_refresh_token=vault.seal_refresh_token(refresh_token)
            )
            database.add(credential)
            await database.flush()
            credential_reference = credential.id
        elif existing is not None:
            credential_reference = existing.credential_reference
        else:
            raise AssertionError("Validated existing Google connection is required")

        if credential_reference is None:
            raise AssertionError("Validated Google credential reference is required")

        if existing is None:
            database.add(
                Connection(
                    user_id=current_account.user.id,
                    workspace_id=current_account.workspace.id,
                    provider="google",
                    external_account_id=external_account_id,
                    external_email=normalized_external_email,
                    status="active",
                    granted_scopes=granted_scopes,
                    credential_reference=credential_reference,
                    access_token_expires_at=expires_at,
                    last_checked_at=datetime.now(UTC),
                )
            )
        else:
            existing.status = "active"
            existing.external_email = normalized_external_email
            existing.granted_scopes = granted_scopes
            existing.credential_reference = credential_reference
            existing.access_token_expires_at = expires_at
            existing.last_checked_at = datetime.now(UTC)
            existing.last_error = None

        if (
            previous_credential_reference is not None
            and previous_credential_reference != credential_reference
        ):
            previous_credential = await database.get(
                ConnectionCredential,
                previous_credential_reference,
            )
            if previous_credential is not None:
                await database.delete(previous_credential)

    return connection_redirect(settings, "connected")


@router.get("/google", response_model=list[ConnectionResponse])
async def list_google_connections(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> list[ConnectionResponse]:
    connections = await database.scalars(
        select(Connection)
        .where(
            Connection.workspace_id == current_account.workspace.id,
            Connection.user_id == current_account.user.id,
            Connection.provider == "google",
        )
        .order_by(Connection.created_at)
    )
    return [response_from_connection(connection) for connection in connections]


@router.post(
    "/google/{connection_id}/health",
    response_model=ConnectionHealthResponse,
)
async def check_google_connection_health(
    connection_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ConnectionHealthResponse:
    connection = await database.scalar(
        select(Connection).where(
            Connection.id == connection_id,
            Connection.workspace_id == current_account.workspace.id,
            Connection.user_id == current_account.user.id,
            Connection.provider == "google",
        )
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google connection not found",
        )
    if connection.credential_reference is None:
        connection.status = "needs_reauthorization"
        connection.last_error = "credential_unavailable"
        connection.last_checked_at = datetime.now(UTC)
        await database.commit()
        return ConnectionHealthResponse(
            **response_from_connection(connection).model_dump(),
            healthy=False,
        )

    credential = await database.get(ConnectionCredential, connection.credential_reference)
    if credential is None:
        connection.status = "needs_reauthorization"
        connection.last_error = "credential_unavailable"
        connection.last_checked_at = datetime.now(UTC)
        await database.commit()
        return ConnectionHealthResponse(
            **response_from_connection(connection).model_dump(),
            healthy=False,
        )

    try:
        refresh_token = google_vault(settings).reveal_refresh_token(
            credential.encrypted_refresh_token
        )
        token_response = await refresh_google_access_token(refresh_token, settings)
        expires_in = token_response.get("expires_in")
        connection.access_token_expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if isinstance(expires_in, int | str) and str(expires_in).isdigit()
            else None
        )
        connection.status = "active"
        connection.last_error = None
        connection.last_checked_at = datetime.now(UTC)
        await database.commit()
        return ConnectionHealthResponse(
            **response_from_connection(connection).model_dump(),
            healthy=True,
        )
    except (CredentialVaultError, GoogleOAuthProviderError):
        connection.status = "needs_reauthorization"
        connection.last_error = "refresh_failed"
        connection.last_checked_at = datetime.now(UTC)
        await database.commit()
        return ConnectionHealthResponse(
            **response_from_connection(connection).model_dump(),
            healthy=False,
        )
