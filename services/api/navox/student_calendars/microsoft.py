"""Separate, session-bound Microsoft calendar consent; never grants mail access."""

from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.connectors.sync_state import utc
from navox.core.credential_vault import CredentialVault, CredentialVaultError
from navox.core.settings import Settings
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt
from navox.db.student_calendars import StudentCalendarGrant
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
    CalendarAuthorization,
    CalendarStart,
)

PURPOSE = "microsoft_calendar_availability"
IDENTITY_SCOPES = ("openid", "profile", "email", "offline_access")
SCOPES = (*IDENTITY_SCOPES, *MICROSOFT_AVAILABILITY_SCOPES)


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
    payload: CalendarStart,
    settings: Settings,
) -> CalendarAuthorization:
    if not configured(settings):
        raise schedule.StudentError(503, "Microsoft calendar connection setup is pending")
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    service.check_command(current, payload)
    state = await service.ensure_state(database, current, user_id, workspace_id)
    existing = await service.grants(database, user_id, workspace_id)
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
    for previous in existing:
        if previous.status == "pending":
            await service.retire(database, previous, current.now)
    nonce, verifier = token_urlsafe(32), token_urlsafe(64)
    attempt = OAuthAuthorizationAttempt(
        user_id=user_id,
        workspace_id=workspace_id,
        provider="microsoft",
        purpose=PURPOSE,
        connection_id=payload.connection_id,
        requested_scopes=list(SCOPES),
        state_hash=sha256(nonce.encode()).hexdigest(),
        code_verifier=verifier,
        expires_at=current.now + timedelta(minutes=10),
    )
    database.add(attempt)
    await database.flush()
    grant = StudentCalendarGrant(
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
        consent_version=CONSENT_VERSION,
        accepted_at=current.now,
    )
    database.add(grant)
    service.advance(state)
    await database.flush()
    result = CalendarAuthorization(
        authorization_url=authorization_url(
            settings,
            state=nonce,
            verifier=verifier,
            scopes=list(SCOPES),
            login_hint=connection.external_email if connection else None,
        ),
        requested_scopes=list(SCOPES),
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
                OAuthAuthorizationAttempt.purpose == PURPOSE,
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
) -> tuple[service.Boundary, StudentCalendarGrant, OAuthAuthorizationAttempt]:
    if not settings.student_calendar_availability_enabled or not configured(settings):
        raise schedule.StudentError(403, "Microsoft calendar access is unavailable")
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    attempt = await database.get(OAuthAuthorizationAttempt, attempt_id, populate_existing=True)
    grant = await database.scalar(
        select(StudentCalendarGrant)
        .where(
            StudentCalendarGrant.oauth_attempt_id == attempt_id,
            StudentCalendarGrant.user_id == user_id,
            StudentCalendarGrant.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        attempt is None
        or grant is None
        or current.state is None
        or attempt.user_id != user_id
        or attempt.workspace_id != workspace_id
        or attempt.provider != "microsoft"
        or attempt.purpose != PURPOSE
        or (attempt.used_at is not None) != consumed
        or utc(attempt.expires_at) <= current.now
        or grant.status != "pending"
        or grant.consent_version != CONSENT_VERSION
        or grant.owner_scope_id != current.state.id
        or grant.generation != current.generation
        or grant.auth_session_id != current.session.id
    ):
        raise schedule.StudentError(409, "This calendar sign-in expired or changed. Start again")
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
    attempt_id = await database.scalar(
        select(OAuthAuthorizationAttempt.id).where(
            OAuthAuthorizationAttempt.state_hash == sha256(state.encode()).hexdigest(),
            OAuthAuthorizationAttempt.user_id == user_id,
            OAuthAuthorizationAttempt.workspace_id == workspace_id,
            OAuthAuthorizationAttempt.provider == "microsoft",
            OAuthAuthorizationAttempt.purpose == PURPOSE,
        )
    )
    if attempt_id is None:
        raise schedule.StudentError(409, "This Microsoft calendar sign-in is unavailable")
    current, _, attempt = await pending(
        database, settings, user_id, workspace_id, session_hash, attempt_id, consumed=False
    )
    verifier = attempt.code_verifier
    attempt.used_at = current.now
    await database.commit()  # One exchange only, even after timeout.

    async def check() -> None:
        await pending(
            database, settings, user_id, workspace_id, session_hash, attempt_id, consumed=True
        )
        await database.commit()

    gateway = MicrosoftMailGateway(check=check)
    tokens = await gateway.token(
        settings, code=code, verifier=verifier, fallback_scopes=frozenset(SCOPES)
    )
    await pending(
        database, settings, user_id, workspace_id, session_hash, attempt_id, consumed=True
    )
    await database.commit()
    identity = await gateway.identity(tokens.access_token)
    if not set(MICROSOFT_AVAILABILITY_SCOPES).issubset(tokens.scopes):
        raise schedule.StudentError(403, "Microsoft did not grant calendar busy-time access")
    current, grant, attempt = await pending(
        database, settings, user_id, workspace_id, session_hash, attempt_id, consumed=True
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
    connection.granted_scopes = sorted(tokens.scopes.intersection(SCOPES))
    connection.last_checked_at = current.now
    await database.flush()
    for old in await service.grants(database, user_id, workspace_id):
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
