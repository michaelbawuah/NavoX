"""Separate, session-bound Microsoft calendar consent; never grants mail access."""

from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.connectors.sync_state import utc
from navox.core.credential_vault import CredentialVault, CredentialVaultError
from navox.core.settings import Settings
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt
from navox.db.student_calendars import StudentCalendarGrant, StudentCalendarWriteGrant
from navox.providers.microsoft_mail import (
    MicrosoftMailError,
    MicrosoftMailGateway,
    authorization_url,
)
from navox.student import service as schedule
from navox.student_calendars import service
from navox.student_calendars.schemas import (
    CONSENT_VERSION,
    MICROSOFT_AVAILABILITY_SCOPES,
    MICROSOFT_SCHEDULING_SCOPES,
    SCHEDULING_CONSENT_VERSION,
    CalendarAuthorization,
    CalendarSchedulingStart,
    CalendarStart,
)

PURPOSE = "microsoft_calendar_availability"
SCHEDULING_PURPOSE = "microsoft_calendar_scheduling"
IDENTITY_SCOPES = ("openid", "profile", "email", "offline_access")
SCOPES = (*IDENTITY_SCOPES, *MICROSOFT_AVAILABILITY_SCOPES)
WRITE_SCOPES = (*IDENTITY_SCOPES, *MICROSOFT_SCHEDULING_SCOPES)


def configured(settings: Settings) -> bool:
    try:
        CredentialVault(settings)
    except CredentialVaultError:
        return False
    return bool(
        settings.microsoft_oauth_client_id.strip()
        and settings.microsoft_oauth_client_secret
        and settings.microsoft_oauth_client_secret.get_secret_value().strip()
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
    if scheduling and (
        not settings.student_calendar_writes_enabled
        or not settings.student_microsoft_calendar_writes_enabled
    ):
        raise schedule.StudentError(404, "Microsoft scheduling authorization is not enabled")
    if payload.consent_version != (SCHEDULING_CONSENT_VERSION if scheduling else CONSENT_VERSION):
        raise schedule.StudentError(422, "Confirm this separate calendar purpose")
    model = StudentCalendarWriteGrant if scheduling else StudentCalendarGrant
    purpose = SCHEDULING_PURPOSE if scheduling else PURPOSE
    scopes = WRITE_SCOPES if scheduling else SCOPES
    if not configured(settings):
        raise schedule.StudentError(503, "Microsoft calendar connection setup is pending")
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    service.check_command(current, payload)
    state = await service.ensure_state(database, current, user_id, workspace_id)
    existing = await service.grants(database, user_id, workspace_id, scheduling=scheduling)
    if len(existing) >= service.MAX_GRANTS or any(
        g.request_id == payload.request_id for g in existing
    ):
        raise schedule.StudentError(409, "This calendar request needs a new review")
    connection = None
    if payload.connection_id is not None:
        connection = await service.owned_connection(
            database, user_id, workspace_id, payload.connection_id
        )
        if (
            connection is None
            or connection.provider != "microsoft"
            or connection.status != "active"
        ):
            raise schedule.StudentError(404, "Microsoft calendar connection not found")
    if scheduling:
        busy = await service.grants(database, user_id, workspace_id)
        if (
            connection is None
            or not service.ready(connection)
            or not any(
                grant.connection_id == connection.id
                and grant.status == "active"
                and grant.owner_scope_id == state.id
                and grant.generation == current.generation
                and grant.consent_version == CONSENT_VERSION
                for grant in busy
            )
        ):
            raise schedule.StudentError(
                409, "Connect current Microsoft busy-time access before scheduling"
            )
    for previous in existing:
        if previous.status == "pending":
            await service.retire(database, previous, current.now)
    nonce, verifier = token_urlsafe(32), token_urlsafe(64)
    attempt = OAuthAuthorizationAttempt(
        user_id=user_id,
        workspace_id=workspace_id,
        provider="microsoft",
        purpose=purpose,
        connection_id=payload.connection_id,
        requested_scopes=list(scopes),
        state_hash=sha256(nonce.encode()).hexdigest(),
        code_verifier=verifier,
        expires_at=current.now + timedelta(minutes=10),
    )
    database.add(attempt)
    await database.flush()
    grant = model(
        owner_scope_id=state.id,
        user_id=user_id,
        workspace_id=workspace_id,
        connection_id=payload.connection_id,
        oauth_attempt_id=attempt.id,
        auth_session_id=current.session.id,
        request_id=payload.request_id,
        generation=current.generation,
        revision=1,
        status="pending",
        consent_version=SCHEDULING_CONSENT_VERSION if scheduling else CONSENT_VERSION,
        accepted_at=current.now,
        **({"provider": "microsoft"} if scheduling else {}),
    )
    database.add(grant)
    service.advance(state)
    await database.flush()
    result = CalendarAuthorization(
        authorization_url=authorization_url(
            settings,
            state=nonce,
            verifier=verifier,
            scopes=list(scopes),
            login_hint=connection.external_email if connection else None,
        ),
        requested_scopes=list(scopes),
        generation=state.generation,
        revision=state.revision,
        grant_id=grant.id,
    )
    schedule.audit(database, user_id, workspace_id, "student.calendar.consent.started", grant.id)
    await database.commit()
    return result


async def handles(database: AsyncSession, user_id: UUID, workspace_id: UUID, state: str) -> bool:
    if not 20 <= len(state) <= 200:
        return False
    return (
        await database.scalar(
            select(OAuthAuthorizationAttempt.id).where(
                OAuthAuthorizationAttempt.state_hash == sha256(state.encode()).hexdigest(),
                OAuthAuthorizationAttempt.user_id == user_id,
                OAuthAuthorizationAttempt.workspace_id == workspace_id,
                OAuthAuthorizationAttempt.provider == "microsoft",
                OAuthAuthorizationAttempt.purpose.in_((PURPOSE, SCHEDULING_PURPOSE)),
            )
        )
        is not None
    )


async def pending(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    attempt_id: UUID,
    *,
    consumed: bool,
    scheduling: bool = False,
) -> tuple[
    service.Boundary, StudentCalendarGrant | StudentCalendarWriteGrant, OAuthAuthorizationAttempt
]:
    if not settings.student_calendar_availability_enabled or not configured(settings):
        raise schedule.StudentError(403, "Microsoft calendar access is unavailable")
    if scheduling and (
        not settings.student_calendar_writes_enabled
        or not settings.student_microsoft_calendar_writes_enabled
    ):
        raise schedule.StudentError(403, "Microsoft scheduling authorization is unavailable")
    model = StudentCalendarWriteGrant if scheduling else StudentCalendarGrant
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    attempt = await database.get(OAuthAuthorizationAttempt, attempt_id, populate_existing=True)
    grant = cast(
        StudentCalendarGrant | StudentCalendarWriteGrant | None,
        await database.scalar(
            select(model)
            .where(
                model.oauth_attempt_id == attempt_id,
                model.user_id == user_id,
                model.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        ),
    )
    if (
        attempt is None
        or grant is None
        or current.state is None
        or attempt.user_id != user_id
        or attempt.workspace_id != workspace_id
        or attempt.provider != "microsoft"
        or attempt.purpose != (SCHEDULING_PURPOSE if scheduling else PURPOSE)
        or attempt.requested_scopes != list(WRITE_SCOPES if scheduling else SCOPES)
        or grant.connection_id != attempt.connection_id
        or (attempt.used_at is not None) != consumed
        or utc(attempt.expires_at) <= current.now
        or grant.status != "pending"
        or grant.consent_version != (SCHEDULING_CONSENT_VERSION if scheduling else CONSENT_VERSION)
        or grant.owner_scope_id != current.state.id
        or grant.generation != current.generation
        or grant.auth_session_id != current.session.id
    ):
        raise schedule.StudentError(409, "This calendar sign-in expired or changed. Start again")
    if scheduling:
        connection = await service.owned_connection(
            database, user_id, workspace_id, attempt.connection_id
        )
        if (
            connection is None
            or connection.provider != "microsoft"
            or not service.ready(connection)
            or not isinstance(grant, StudentCalendarWriteGrant)
            or grant.provider != "microsoft"
            or not any(
                item.connection_id == connection.id
                and item.status == "active"
                and item.owner_scope_id == current.state.id
                and item.generation == current.generation
                and item.consent_version == CONSENT_VERSION
                for item in await service.grants(database, user_id, workspace_id)
            )
        ):
            raise schedule.StudentError(
                409, "Busy-time permission changed before scheduling consent"
            )
    return current, grant, attempt


async def finish(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    *,
    state: str,
    code: str,
) -> None:
    if not 20 <= len(state) <= 200 or not 1 <= len(code) <= 8192:
        raise schedule.StudentError(400, "This Microsoft calendar sign-in is invalid")
    match = (
        await database.execute(
            select(OAuthAuthorizationAttempt.id, OAuthAuthorizationAttempt.purpose).where(
                OAuthAuthorizationAttempt.state_hash == sha256(state.encode()).hexdigest(),
                OAuthAuthorizationAttempt.user_id == user_id,
                OAuthAuthorizationAttempt.workspace_id == workspace_id,
                OAuthAuthorizationAttempt.provider == "microsoft",
                OAuthAuthorizationAttempt.purpose.in_((PURPOSE, SCHEDULING_PURPOSE)),
            )
        )
    ).one_or_none()
    if match is None:
        raise schedule.StudentError(409, "This Microsoft calendar sign-in is unavailable")
    attempt_id, purpose = match
    scheduling = purpose == SCHEDULING_PURPOSE
    scopes = WRITE_SCOPES if scheduling else SCOPES
    required = MICROSOFT_SCHEDULING_SCOPES if scheduling else MICROSOFT_AVAILABILITY_SCOPES
    current, _, attempt = await pending(
        database,
        settings,
        user_id,
        workspace_id,
        session_hash,
        attempt_id,
        consumed=False,
        scheduling=scheduling,
    )
    verifier = attempt.code_verifier
    attempt.used_at = current.now
    await database.commit()  # One exchange only, even after timeout.

    async def check() -> None:
        await pending(
            database,
            settings,
            user_id,
            workspace_id,
            session_hash,
            attempt_id,
            consumed=True,
            scheduling=scheduling,
        )
        await database.commit()

    gateway = MicrosoftMailGateway(check=check)
    tokens = await gateway.token(
        settings, code=code, verifier=verifier, fallback_scopes=frozenset(scopes)
    )
    await pending(
        database,
        settings,
        user_id,
        workspace_id,
        session_hash,
        attempt_id,
        consumed=True,
        scheduling=scheduling,
    )
    await database.commit()
    identity = await gateway.identity(tokens.access_token)
    if not set(required).issubset(tokens.scopes):
        raise schedule.StudentError(403, "Microsoft did not grant the requested calendar purpose")
    current, grant, attempt = await pending(
        database,
        settings,
        user_id,
        workspace_id,
        session_hash,
        attempt_id,
        consumed=True,
        scheduling=scheduling,
    )
    # A distinct purpose identity cannot attach calendar access to an email connector.
    external_id = "calendar:" + identity.account_id
    query = select(Connection).where(
        Connection.user_id == user_id,
        Connection.workspace_id == workspace_id,
        Connection.provider == "microsoft",
        Connection.external_account_id == external_id,
    )
    connection = await database.scalar(
        query.with_for_update().execution_options(populate_existing=True)
    )
    if attempt.connection_id is not None and (
        connection is None
        or connection.id != attempt.connection_id
        or connection.external_email != identity.email.casefold()
    ):
        raise schedule.StudentError(
            409, "Use the same Microsoft calendar account when reconnecting"
        )
    if connection is not None and connection.status != "active":
        raise schedule.StudentError(409, "This calendar connection was disconnected. Start again")
    credential = ConnectionCredential(
        encrypted_refresh_token=CredentialVault(settings).seal_refresh_token(tokens.refresh_token)
    )
    database.add(credential)
    await database.flush()
    if connection is None:
        connection = Connection(
            user_id=user_id,
            workspace_id=workspace_id,
            provider="microsoft",
            external_account_id=external_id,
            external_email=identity.email.casefold(),
            status="active",
        )
        database.add(connection)
    old_credential = connection.credential_reference
    connection.external_email = identity.email.casefold()
    connection.credential_reference = credential.id
    connection.granted_scopes = sorted(tokens.scopes.intersection(scopes))
    connection.last_checked_at = current.now
    await database.flush()
    for old in await service.grants(database, user_id, workspace_id, scheduling=scheduling):
        if old.id != grant.id and old.connection_id == connection.id and old.status != "revoked":
            await service.retire(database, old, current.now)
    grant.connection_id, grant.status = connection.id, "active"
    grant.revision += 1
    assert current.state is not None
    service.advance(current.state)
    if old_credential:
        # Delete only an unreferenced old vault record; never another connection's secret.
        reference = await database.scalar(
            select(Connection.id).where(Connection.credential_reference == old_credential).limit(1)
        )
        if reference is None:
            previous_credential = await database.get(ConnectionCredential, old_credential)
            if previous_credential:
                await database.delete(previous_credential)
    schedule.audit(database, user_id, workspace_id, "student.calendar.consent.completed", grant.id)
    await database.commit()


async def access(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    snapshot: service.CalendarScope,
    grant: service.CalendarGrantSnapshot,
    session_hash: str,
) -> str:
    async def check() -> None:
        async with factory() as database:
            await service.check_scope(database, snapshot, session_hash)
            await database.commit()

    async with factory() as database:
        await service.check_scope(database, snapshot, session_hash)
        connection = await service.owned_connection(
            database, snapshot.user_id, snapshot.workspace_id, grant.connection_id
        )
        credential = (
            await database.get(ConnectionCredential, grant.credential_id)
            if grant.credential_id
            else None
        )
        if connection is None or connection.provider != "microsoft" or credential is None:
            raise MicrosoftMailError("reauthorize")
        refresh = CredentialVault(settings).reveal_refresh_token(credential.encrypted_refresh_token)
        await database.commit()
    gateway = MicrosoftMailGateway(check=check)
    tokens = await gateway.token(settings, refresh=refresh, fallback_scopes=grant.granted_scopes)
    await check()
    identity = await gateway.identity(tokens.access_token)
    async with factory() as database:
        await service.check_scope(database, snapshot, session_hash)
        connection = await service.owned_connection(
            database, snapshot.user_id, snapshot.workspace_id, grant.connection_id
        )
        if (
            connection is None
            or connection.external_account_id != "calendar:" + identity.account_id
            or connection.external_email != identity.email.casefold()
        ):
            raise MicrosoftMailError("reauthorize")
        credential = await database.get(ConnectionCredential, grant.credential_id)
        if credential is None:
            raise MicrosoftMailError("reauthorize")
        credential.encrypted_refresh_token = CredentialVault(settings).seal_refresh_token(
            tokens.refresh_token
        )
        retained = tokens.scopes.intersection(grant.granted_scopes)
        connection.granted_scopes = sorted(retained)
        await database.commit()
        if (
            not set(MICROSOFT_AVAILABILITY_SCOPES).issubset(retained)
            or retained != grant.granted_scopes
        ):
            raise schedule.StudentError(
                409, "Calendar permissions changed. Reconnect before checking"
            )
    return tokens.access_token
