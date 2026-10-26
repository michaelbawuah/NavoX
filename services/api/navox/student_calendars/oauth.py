"""Separate read-busy consent using the existing encrypted Google credential store."""

import asyncio
import json
from datetime import timedelta
from secrets import token_urlsafe
from typing import Any, cast
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.builtin.google import ensure_google_connector_connection
from navox.connectors.sync_state import utc
from navox.core.settings import Settings
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt
from navox.db.student_calendars import StudentCalendarGrant, StudentCalendarWriteGrant
from navox.student import service as schedule
from navox.student_calendars import service
from navox.student_calendars.schemas import (
    AVAILABILITY_SCOPES,
    CONSENT_VERSION,
    SCHEDULING_CONSENT_VERSION,
    SCHEDULING_SCOPES,
    CalendarAuthorization,
    CalendarSchedulingStart,
    CalendarStart,
)

PURPOSE = "calendar_availability"
SCHEDULING_PURPOSE = "calendar_scheduling"


async def _json_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> dict[str, Any]:
    from navox.api.connections import GoogleOAuthProviderError

    try:
        async with (
            asyncio.timeout(12),
            httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client,
        ):
            async with client.stream(method, url, headers=headers, data=data) as response:
                if response.status_code != 200:
                    raise GoogleOAuthProviderError("Calendar authorization failed")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 65536:
                        raise GoogleOAuthProviderError("Calendar authorization failed")
                    raw.extend(chunk)

                def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                    value: dict[str, Any] = {}
                    for key, item in pairs:
                        if key in value:
                            raise ValueError
                        value[key] = item
                    return value

                value = json.loads(raw, object_pairs_hook=unique)
                if not isinstance(value, dict):
                    raise GoogleOAuthProviderError("Calendar authorization failed")
                return cast(dict[str, Any], value)
    except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
        raise GoogleOAuthProviderError("Calendar authorization failed") from None


async def exchange_authorization_code(
    code: str, verifier: str, settings: Settings
) -> dict[str, Any]:
    from navox.api.connections import GOOGLE_TOKEN_ENDPOINT, google_client_secret

    return await _json_request(
        "POST",
        GOOGLE_TOKEN_ENDPOINT,
        data={
            "client_id": settings.google_oauth_client_id,
            "client_secret": google_client_secret(settings),
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": settings.google_oauth_redirect_uri,
        },
    )


async def fetch_profile(token: str) -> dict[str, Any]:
    from navox.api.connections import GOOGLE_USERINFO_ENDPOINT

    return await _json_request(
        "GET", GOOGLE_USERINFO_ENDPOINT, headers={"Authorization": "Bearer " + token}
    )


async def start(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    payload: CalendarStart | CalendarSchedulingStart,
    settings: Settings,
    *,
    scheduling: bool = False,
) -> CalendarAuthorization:
    from navox.api.connections import (
        GOOGLE_ACCOUNT_BINDING_SCOPES,
        OAUTH_ATTEMPT_TTL,
        authorization_url,
        google_vault,
        hash_value,
    )

    if scheduling and not getattr(settings, "student_calendar_writes_enabled", False):
        raise schedule.StudentError(404, "Calendar scheduling is not enabled")
    google_vault(settings)
    model = StudentCalendarWriteGrant if scheduling else StudentCalendarGrant
    consent = SCHEDULING_CONSENT_VERSION if scheduling else CONSENT_VERSION
    if payload.consent_version != consent:
        raise schedule.StudentError(422, "Confirm the selected calendar purpose")
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    service.check_command(current, payload)
    row = await service.ensure_state(database, current, user_id, workspace_id)
    previous = await database.scalar(
        select(model.id).where(
            model.owner_scope_id == row.id,
            model.request_id == payload.request_id,
        )
    )
    if previous is not None:
        raise schedule.StudentError(
            409, "Calendar authorization already started. Refresh before retrying"
        )
    existing_grants = await service.grants(database, user_id, workspace_id, scheduling=scheduling)
    if len(existing_grants) >= service.MAX_GRANTS:
        raise schedule.StudentError(
            409, "Reset calendar permissions before starting another connection"
        )
    if payload.connection_id is not None:
        connection = await service.owned_connection(
            database, user_id, workspace_id, payload.connection_id
        )
        if connection is None or connection.provider != "google" or connection.status != "active":
            raise schedule.StudentError(404, "Active Google connection not found")
    if scheduling:
        if (
            payload.connection_id is None
            or not service.ready(connection)
            or not any(
                grant.connection_id == payload.connection_id
                and grant.status == "active"
                and grant.generation == current.generation
                and grant.owner_scope_id == row.id
                and grant.consent_version == CONSENT_VERSION
                for grant in await service.grants(database, user_id, workspace_id)
            )
        ):
            raise schedule.StudentError(409, "Connect busy-time access before scheduling")
    # Only one authorization window is current; a failed/abandoned earlier
    # window cannot leave an unknown account in every later availability result.
    for old in existing_grants:
        if old.status == "pending":
            await service.retire(database, old, current.now)
    nonce, verifier = token_urlsafe(32), token_urlsafe(64)
    scopes = [
        *GOOGLE_ACCOUNT_BINDING_SCOPES,
        *(SCHEDULING_SCOPES if scheduling else AVAILABILITY_SCOPES),
    ]
    attempt = OAuthAuthorizationAttempt(
        user_id=user_id,
        workspace_id=workspace_id,
        provider="google",
        purpose=SCHEDULING_PURPOSE if scheduling else PURPOSE,
        connection_id=payload.connection_id,
        requested_scopes=scopes,
        state_hash=hash_value(nonce),
        code_verifier=verifier,
        expires_at=current.now + OAUTH_ATTEMPT_TTL,
    )
    database.add(attempt)
    await database.flush()
    grant = model(
        owner_scope_id=row.id,
        user_id=user_id,
        workspace_id=workspace_id,
        connection_id=payload.connection_id,
        oauth_attempt_id=attempt.id,
        auth_session_id=current.session.id,
        request_id=payload.request_id,
        generation=current.generation,
        revision=1,
        status="pending",
        consent_version=consent,
        accepted_at=current.now,
    )
    database.add(grant)
    service.advance(row)
    await database.flush()
    result = CalendarAuthorization(
        authorization_url=authorization_url(
            nonce,
            verifier,
            settings,
            scopes=tuple(scopes),
            include_granted_scopes=True,
        ),
        requested_scopes=scopes,
        generation=row.generation,
        revision=row.revision,
        grant_id=grant.id,
    )
    schedule.audit(database, user_id, workspace_id, "student.calendar.consent.started", grant.id)
    await database.commit()
    return result


async def pending(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    attempt_id: UUID,
    settings: Settings,
    *,
    scheduling: bool = False,
) -> tuple[
    service.Boundary, StudentCalendarGrant | StudentCalendarWriteGrant, OAuthAuthorizationAttempt
]:
    if not settings.student_calendar_availability_enabled:
        raise schedule.StudentError(404, "Calendar availability is not enabled")
    if scheduling and not getattr(settings, "student_calendar_writes_enabled", False):
        raise schedule.StudentError(404, "Calendar scheduling is not enabled")
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    model = StudentCalendarWriteGrant if scheduling else StudentCalendarGrant
    grant = cast(
        StudentCalendarGrant | StudentCalendarWriteGrant | None,
        await database.scalar(
            select(model)
            .where(
                model.user_id == user_id,
                model.workspace_id == workspace_id,
                model.oauth_attempt_id == attempt_id,
            )
            .execution_options(populate_existing=True)
        ),
    )
    attempt = await database.scalar(
        select(OAuthAuthorizationAttempt)
        .where(
            OAuthAuthorizationAttempt.id == attempt_id,
            OAuthAuthorizationAttempt.user_id == user_id,
            OAuthAuthorizationAttempt.workspace_id == workspace_id,
            OAuthAuthorizationAttempt.purpose == (SCHEDULING_PURPOSE if scheduling else PURPOSE),
            OAuthAuthorizationAttempt.provider == "google",
        )
        .execution_options(populate_existing=True)
    )
    if (
        grant is None
        or attempt is None
        or current.state is None
        or grant.owner_scope_id != current.state.id
        or grant.generation != current.generation
        or grant.status != "pending"
        or grant.consent_version != (SCHEDULING_CONSENT_VERSION if scheduling else CONSENT_VERSION)
        or grant.auth_session_id != current.session.id
        or utc(attempt.expires_at) <= current.now
    ):
        raise schedule.StudentError(409, "Calendar authorization is no longer available")
    if scheduling:
        connection = await service.owned_connection(
            database, user_id, workspace_id, grant.connection_id
        )
        if not service.ready(connection) or not any(
            busy.connection_id == grant.connection_id
            and busy.status == "active"
            and busy.generation == current.generation
            and busy.consent_version == CONSENT_VERSION
            for busy in await service.grants(database, user_id, workspace_id)
        ):
            raise schedule.StudentError(
                409, "Busy-time permission changed. Review it before scheduling"
            )
    return current, grant, attempt


async def complete(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    attempt_id: UUID,
    token_response: dict[str, Any],
    settings: Settings,
    *,
    scheduling: bool = False,
) -> str:
    from navox.api.connections import (
        GOOGLE_ACCOUNT_BINDING_SCOPES,
        GOOGLE_ALLOWED_SCOPES,
        google_vault,
    )

    token = token_response.get("access_token")
    scope_text = token_response.get("scope")
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= 16384
        or not isinstance(scope_text, str)
        or len(scope_text) > 8192
    ):
        return "scope_mismatch"
    returned = set(scope_text.split())
    required = {
        *GOOGLE_ACCOUNT_BINDING_SCOPES,
        *(SCHEDULING_SCOPES if scheduling else AVAILABILITY_SCOPES),
    }
    if not required.issubset(returned) or not returned.issubset(GOOGLE_ALLOWED_SCOPES):
        return "scope_mismatch"
    await pending(
        database, user_id, workspace_id, session_hash, attempt_id, settings, scheduling=scheduling
    )
    await database.commit()  # Never hold native authority locks over provider I/O.
    profile = await fetch_profile(token)
    account_id, email = profile.get("sub"), profile.get("email")
    if (
        not isinstance(account_id, str)
        or not 1 <= len(account_id) <= 256
        or not isinstance(email, str)
        or not 1 <= len(email.strip()) <= 320
        or any(ord(char) < 32 or ord(char) == 127 for char in account_id + email)
        or not (
            profile.get("email_verified") is True
            or type(profile.get("email_verified")) is str
            and profile["email_verified"] == "true"
        )
    ):
        return "unverified"
    normalized = email.strip().casefold()
    current, grant, attempt = await pending(
        database, user_id, workspace_id, session_hash, attempt_id, settings, scheduling=scheduling
    )
    query = select(Connection).where(
        Connection.user_id == user_id,
        Connection.workspace_id == workspace_id,
        Connection.provider == "google",
    )
    if attempt.connection_id is not None:
        query = query.where(Connection.id == attempt.connection_id)
    else:
        query = query.where(Connection.external_account_id == account_id)
    connection = await database.scalar(
        query.with_for_update().execution_options(populate_existing=True)
    )
    if attempt.connection_id is not None and (
        connection is None
        or connection.external_account_id != account_id
        or connection.external_email != normalized
    ):
        return "account_mismatch"
    if connection is not None and connection.status != "active":
        return "failed"
    refresh = token_response.get("refresh_token")
    if refresh is not None and (not isinstance(refresh, str) or not 1 <= len(refresh) <= 16384):
        return "failed"
    old_credential = connection.credential_reference if connection is not None else None
    if refresh is None and (
        old_credential is None or await database.get(ConnectionCredential, old_credential) is None
    ):
        return "refresh_token_required"
    credential_id = old_credential
    if isinstance(refresh, str):
        credential = ConnectionCredential(
            encrypted_refresh_token=google_vault(settings).seal_refresh_token(refresh)
        )
        database.add(credential)
        await database.flush()
        credential_id = credential.id
    if connection is None:
        connection = Connection(
            user_id=user_id,
            workspace_id=workspace_id,
            provider="google",
            external_account_id=account_id,
            external_email=normalized,
            status="active",
        )
        database.add(connection)
    connection.credential_reference = credential_id
    connection.granted_scopes = sorted(returned)  # Provider narrowing wins; never union old grants.
    expiry = token_response.get("expires_in")
    connection.access_token_expires_at = (
        current.now + timedelta(seconds=int(expiry))
        if isinstance(expiry, int | str)
        and not isinstance(expiry, bool)
        and len(str(expiry)) <= 6
        and str(expiry).isdigit()
        and 0 < int(expiry) <= 86400
        else None
    )
    connection.last_checked_at, connection.last_error = current.now, None
    await database.flush()
    await ensure_google_connector_connection(database, connection)
    for old in await service.grants(database, user_id, workspace_id, scheduling=scheduling):
        if old.id != grant.id and old.connection_id == connection.id and old.status != "revoked":
            await service.retire(database, old, current.now)
    grant.connection_id, grant.status = connection.id, "active"
    grant.revision += 1
    assert current.state is not None
    service.advance(current.state)
    if old_credential is not None and old_credential != credential_id:
        await database.flush()
        referenced = await database.scalar(
            select(Connection.id).where(Connection.credential_reference == old_credential).limit(1)
        )
        if referenced is None:
            previous = await database.get(ConnectionCredential, old_credential)
            if previous is not None:
                await database.delete(previous)
    schedule.audit(database, user_id, workspace_id, "student.calendar.consent.completed", grant.id)
    await database.commit()
    return "calendar_scheduling_enabled" if scheduling else "calendar_availability_enabled"
