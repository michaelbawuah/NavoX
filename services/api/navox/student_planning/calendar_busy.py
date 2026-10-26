"""Calendar busy intervals for reviewed study plans; never writes provider events.

Provider reads share the caller's planning transaction and owner lock. This module
never commits, rolls back, or opens a second transaction while collecting facts.
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.credential_vault import CredentialVault, CredentialVaultError
from navox.core.settings import Settings, get_settings
from navox.db.apple_calendars import AppleCalendarGrant
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt
from navox.db.student_calendars import StudentCalendarState
from navox.providers.apple_availability import AppleAvailabilityProvider, AppleAvailabilitySnapshot
from navox.providers.google_availability import (
    GoogleAvailabilityProvider,
    GoogleAvailabilitySnapshot,
)
from navox.providers.google_oauth import GoogleAccessTokenError, access_token_for_connection
from navox.providers.microsoft_availability import (
    MicrosoftAvailabilityProvider,
    MicrosoftAvailabilitySnapshot,
)
from navox.providers.microsoft_mail import MicrosoftMailError, MicrosoftMailGateway
from navox.student import service as schedule
from navox.student_calendars import apple
from navox.student_calendars import service as calendars
from navox.student_calendars.schemas import CONSENT_VERSION, MICROSOFT_AVAILABILITY_SCOPES


@dataclass(frozen=True)
class CalendarBusy:
    intervals: tuple[tuple[datetime, datetime], ...]
    digest: str
    connected: bool
    warnings: tuple[str, ...]


def _identity(connection: Connection | None) -> str | None:
    if connection is None:
        return None
    return hashlib.sha256(
        json.dumps(
            [connection.provider, connection.external_account_id, connection.external_email],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _entry(
    state: StudentCalendarState,
    connection: Connection | None,
    *,
    grant_id: UUID,
    revision: int,
    status: str,
    provider: str,
) -> dict[str, Any]:
    return {
        "id": str(grant_id),
        "provider": provider,
        "revision": revision,
        "status": status,
        "generation": state.generation,
        "state_revision": state.revision,
        "connection_id": str(connection.id) if connection else None,
        "credential_id": str(connection.credential_reference)
        if connection and connection.credential_reference
        else None,
        "connection_status": connection.status if connection else None,
        "identity_digest": _identity(connection),
        "scopes": sorted(connection.granted_scopes) if connection else [],
    }


async def inventory(
    database: AsyncSession, user_id: UUID, workspace_id: UUID
) -> list[dict[str, Any]]:
    state = await calendars.state(database, user_id, workspace_id)
    result = []
    for grant in await calendars.grants(database, user_id, workspace_id):
        if grant.status == "revoked":
            continue
        connection = await calendars.owned_connection(
            database, user_id, workspace_id, grant.connection_id
        )
        if (
            state is None
            or grant.owner_scope_id != state.id
            or grant.generation != state.generation
            or grant.consent_version != CONSENT_VERSION
            or (connection is not None and connection.provider not in {"google", "microsoft"})
        ):
            raise schedule.StudentError(
                409, "Review calendar access in Connections before planning"
            )
        attempt = await database.get(OAuthAuthorizationAttempt, grant.oauth_attempt_id)
        provider = (
            connection.provider if connection else (attempt.provider if attempt else "unknown")
        )
        result.append(
            _entry(
                state,
                connection,
                grant_id=grant.id,
                revision=grant.revision,
                status=grant.status,
                provider=provider,
            )
        )
    apple_grants = list(
        await database.scalars(
            select(AppleCalendarGrant)
            .where(
                AppleCalendarGrant.user_id == user_id,
                AppleCalendarGrant.workspace_id == workspace_id,
            )
            .order_by(AppleCalendarGrant.id)
            .limit(apple.MAX_ACCOUNTS + 1)
            .execution_options(populate_existing=True)
        )
    )
    if len(apple_grants) > apple.MAX_ACCOUNTS:
        raise schedule.StudentError(409, "Review Apple calendar connections before planning")
    for apple_grant in apple_grants:
        connection = await calendars.owned_connection(
            database, user_id, workspace_id, apple_grant.connection_id
        )
        if (
            state is None
            or apple_grant.owner_scope_id != state.id
            or apple_grant.generation != state.generation
            or apple_grant.consent_version != apple.CONSENT_VERSION
            or (connection is not None and connection.provider != "icloud")
        ):
            raise schedule.StudentError(
                409, "Review Apple calendar access in Connections before planning"
            )
        result.append(
            _entry(
                state,
                connection,
                grant_id=apple_grant.id,
                revision=apple_grant.revision,
                status="active",
                provider="icloud",
            )
        )
    return sorted(result, key=lambda row: row["id"])


async def _scope(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, original: list[dict[str, Any]]
) -> calendars.CalendarScope:
    """Build the Apple vault envelope authority from the exact collected inventory."""
    snapshots = []
    for row in original:
        connection = await calendars.owned_connection(
            database,
            user_id,
            workspace_id,
            UUID(row["connection_id"]) if row["connection_id"] else None,
        )
        if _identity(connection) != row["identity_digest"]:
            raise schedule.StudentError(409, "Calendar access changed. Review a new plan")
        snapshots.append(
            calendars.CalendarGrantSnapshot(
                id=UUID(row["id"]),
                revision=row["revision"],
                status=row["status"],
                connection_id=UUID(row["connection_id"]) if row["connection_id"] else None,
                credential_id=UUID(row["credential_id"]) if row["credential_id"] else None,
                external_account_id=connection.external_account_id if connection else None,
                granted_scopes=frozenset(row["scopes"]),
                connection_status=row["connection_status"],
                provider=row["provider"],
            )
        )
    return calendars.CalendarScope(
        user_id,
        workspace_id,
        original[0]["generation"],
        original[0]["state_revision"],
        tuple(snapshots),
    )


async def _microsoft_token(
    database: AsyncSession,
    connection: Connection,
    settings: Settings,
    check: Callable[[], Awaitable[None]],
) -> str:
    credential = await database.get(ConnectionCredential, connection.credential_reference)
    if credential is None:
        raise MicrosoftMailError("reauthorize")
    original_scopes = frozenset(connection.granted_scopes)
    gateway = MicrosoftMailGateway(check=check)
    vault = CredentialVault(settings)
    refresh = vault.reveal_refresh_token(credential.encrypted_refresh_token)
    await check()
    tokens = await gateway.token(settings, refresh=refresh, fallback_scopes=original_scopes)
    await check()
    identity = await gateway.identity(tokens.access_token)
    await check()
    if (
        connection.external_account_id != "calendar:" + identity.account_id
        or connection.external_email != identity.email.casefold()
    ):
        raise MicrosoftMailError("reauthorize")
    retained = tokens.scopes.intersection(original_scopes)
    if not set(MICROSOFT_AVAILABILITY_SCOPES).issubset(retained) or retained != original_scopes:
        raise schedule.StudentError(409, "Calendar permissions changed. Reconnect before planning")
    # Microsoft documents that using a refresh token does not revoke the old
    # token. A rolled-back preview therefore retains a usable prior credential,
    # subject to ordinary expiry/revocation. Never commit a nested transaction.
    # https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens
    credential.encrypted_refresh_token = vault.seal_refresh_token(tokens.refresh_token)
    connection.granted_scopes = sorted(retained)
    connection.last_checked_at, connection.last_error = datetime.now(UTC), None
    return tokens.access_token


async def collect(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    start_at: datetime,
    end_at: datetime,
    travel_minutes: int,
) -> CalendarBusy:
    original = await inventory(database, user_id, workspace_id)
    if not original:
        return CalendarBusy(
            (),
            "unconnected",
            False,
            (
                "No calendar is connected. This preview uses your saved classes, "
                "shifts and routine only.",
            ),
        )
    if len(original) > 8:
        raise schedule.StudentError(409, "Narrow calendar connections before planning")
    settings = get_settings()
    intervals: list[tuple[datetime, datetime]] = []

    async def check() -> None:
        user = await schedule.owner(database, user_id, workspace_id)
        if user.agent_paused:
            raise schedule.StudentError(
                409, "Resume NavoX to check connected calendars before planning"
            )
        if await inventory(database, user_id, workspace_id) != original:
            raise schedule.StudentError(409, "Calendar access changed. Review a new plan")

    await check()
    first = (start_at - timedelta(minutes=travel_minutes)).replace(microsecond=0)
    last = end_at + timedelta(minutes=travel_minutes)
    if last.microsecond:
        last = last.replace(microsecond=0) + timedelta(seconds=1)
    try:
        async with asyncio.timeout(45):
            for row in original:
                await check()
                connection = await calendars.owned_connection(
                    database,
                    user_id,
                    workspace_id,
                    UUID(row["connection_id"]) if row["connection_id"] else None,
                )
                if row["status"] != "active" or not calendars.ready(connection):
                    raise schedule.StudentError(
                        409, "Reconnect your calendar before planning; its free time is unknown"
                    )
                assert connection is not None
                value: (
                    AppleAvailabilitySnapshot
                    | GoogleAvailabilitySnapshot
                    | MicrosoftAvailabilitySnapshot
                )
                if row["provider"] == "icloud":
                    scope = await _scope(database, user_id, workspace_id, original)
                    grant = next(item for item in scope.grants if item.id == UUID(row["id"]))
                    account, password = await apple.credentials(database, scope, grant, settings)
                    await check()
                    value = await AppleAvailabilityProvider().collect(
                        apple_id=account,
                        app_password=password,
                        start_at=first,
                        end_at=last,
                        check=check,
                    )
                elif row["provider"] == "microsoft":
                    token = await _microsoft_token(database, connection, settings, check)
                    value = await MicrosoftAvailabilityProvider().collect(
                        access_token=token, start_at=first, end_at=last, check=check
                    )
                elif row["provider"] == "google":
                    detached = Connection(
                        id=connection.id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        provider="google",
                        status="active",
                        external_account_id=connection.external_account_id,
                        external_email=connection.external_email,
                        granted_scopes=list(connection.granted_scopes),
                        credential_reference=connection.credential_reference,
                    )
                    token = await access_token_for_connection(
                        database, connection=detached, settings=settings
                    )
                    await check()
                    if not calendars.ready(detached):
                        raise schedule.StudentError(
                            409, "Calendar permissions changed. Reconnect before planning"
                        )
                    value = await GoogleAvailabilityProvider().collect(
                        access_token=token, start_at=first, end_at=last, check=check
                    )
                else:
                    raise schedule.StudentError(
                        409, "This calendar cannot be checked before planning"
                    )
                await check()
                if value.state != "complete":
                    raise schedule.StudentError(
                        409, "Your calendar could not be fully checked. Try planning again"
                    )
                intervals.extend(
                    (
                        item.start_at - timedelta(minutes=travel_minutes),
                        item.end_at + timedelta(minutes=travel_minutes),
                    )
                    for item in value.busy
                )
            await check()
    except (GoogleAccessTokenError, MicrosoftMailError, CredentialVaultError, TimeoutError):
        await check()
        raise schedule.StudentError(
            503, "Your calendar could not be checked. Reconnect or try again"
        ) from None
    values = tuple(sorted(set(intervals)))
    digest = hashlib.sha256(
        json.dumps(
            {"access": original, "busy": [[a.isoformat(), b.isoformat()] for a, b in values]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return CalendarBusy(
        values,
        digest,
        True,
        (f"Calendar meetings include your {travel_minutes}-minute travel buffer on both sides.",),
    )
