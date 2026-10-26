"""Native Microsoft calendar collection inside its caller's planning transaction.

All provider responses are synthetic. Microsoft documents that refresh does not
revoke the previous refresh token, so rolling back preview metadata can safely
retain it subject to ordinary expiry/revocation:
https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import select
from test_apple_calendar_connection import apple_env as shared_apple_fixture

from navox.connectors.sync_state import database_now
from navox.core.credential_vault import CredentialVault
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt, UserSession
from navox.db.student_calendars import StudentCalendarGrant, StudentCalendarState
from navox.providers.google_availability import ProviderBusy
from navox.providers.microsoft_availability import MicrosoftAvailabilitySnapshot
from navox.providers.microsoft_mail import MailIdentity, MailToken, MicrosoftMailError
from navox.student import service as schedule
from navox.student_calendars import microsoft
from navox.student_calendars.schemas import CONSENT_VERSION
from navox.student_planning import calendar_busy

apple_env = shared_apple_fixture
OLD_REFRESH = "synthetic-ms-old-refresh-private"
NEW_REFRESH = "synthetic-ms-replacement-refresh-private"
ACCESS = "synthetic-ms-access-private"
ACCOUNT = "synthetic-ms-account-private"
EMAIL = "synthetic-ms@example.com"
START = datetime(2026, 10, 27, 7, tzinfo=UTC)
BUSY_START = START.replace(hour=10)
BUSY_END = START.replace(hour=11)


@pytest_asyncio.fixture
async def microsoft_busy_env(apple_env, monkeypatch):
    env = apple_env
    env.settings.microsoft_oauth_client_id = "synthetic-ms-calendar-client"
    env.settings.microsoft_oauth_client_secret = SecretStr("synthetic-ms-client-private")
    env.microsoft = SimpleNamespace(
        state="complete",
        account=ACCOUNT,
        email=EMAIL,
        scopes=frozenset(microsoft.SCOPES),
        token_calls=0,
        identity_calls=0,
        busy_calls=0,
        failure=None,
        during=None,
    )
    async with env.factory() as database:
        now = await database_now(database)
        session_id = await database.scalar(
            select(UserSession.id).where(
                UserSession.user_id == env.user_id,
                UserSession.token_hash == env.token_hash,
            )
        )
        state = StudentCalendarState(
            user_id=env.user_id, workspace_id=env.workspace_id, generation=0, revision=1
        )
        credential = ConnectionCredential(
            encrypted_refresh_token=CredentialVault(env.settings).seal_refresh_token(OLD_REFRESH)
        )
        database.add_all([state, credential])
        await database.flush()
        connection = Connection(
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="microsoft",
            status="active",
            external_account_id="calendar:" + ACCOUNT,
            external_email=EMAIL,
            granted_scopes=list(microsoft.SCOPES),
            credential_reference=credential.id,
        )
        database.add(connection)
        await database.flush()
        attempt = OAuthAuthorizationAttempt(
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="microsoft",
            purpose=microsoft.PURPOSE,
            connection_id=connection.id,
            requested_scopes=list(microsoft.SCOPES),
            state_hash=uuid4().hex,
            code_verifier="synthetic-ms-verifier",
            expires_at=now + timedelta(minutes=10),
            used_at=now,
        )
        database.add(attempt)
        await database.flush()
        grant = StudentCalendarGrant(
            owner_scope_id=state.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=connection.id,
            oauth_attempt_id=attempt.id,
            auth_session_id=session_id,
            request_id=uuid4(),
            generation=0,
            revision=1,
            status="active",
            consent_version=CONSENT_VERSION,
            accepted_at=now,
        )
        database.add(grant)
        await database.commit()
        env.credential_id, env.grant_id, env.state_id = credential.id, grant.id, state.id

    class Gateway:
        def __init__(self, *, check):
            self.check = check

        async def token(self, settings, *, refresh, fallback_scopes):
            assert settings is env.settings
            assert refresh == OLD_REFRESH
            assert fallback_scopes == frozenset(microsoft.SCOPES)
            env.microsoft.token_calls += 1
            await self.check()
            if env.microsoft.failure == "token":
                if env.microsoft.during:
                    await env.microsoft.during()
                # The actual gateway does not run its success-only after-check
                # when the provider fails. The caller must do so on its error path.
                raise MicrosoftMailError("synthetic-private-provider-body")
            await self.check()
            return MailToken(ACCESS, NEW_REFRESH, env.microsoft.scopes)

        async def identity(self, token):
            assert token == ACCESS
            env.microsoft.identity_calls += 1
            await self.check()
            await self.check()
            return MailIdentity("synthetic-subject", env.microsoft.account, env.microsoft.email)

    class BusyProvider:
        async def collect(self, *, access_token, start_at, end_at, check):
            assert access_token == ACCESS
            assert start_at == START - timedelta(minutes=15)
            assert end_at == START.replace(hour=23) + timedelta(minutes=15)
            env.microsoft.busy_calls += 1
            await check()
            if env.microsoft.failure == "busy":
                if env.microsoft.during:
                    await env.microsoft.during()
                raise MicrosoftMailError("synthetic-private-provider-body")
            await check()
            return MicrosoftAvailabilitySnapshot(
                env.microsoft.state, 2, (ProviderBusy(BUSY_START, BUSY_END),), datetime.now(UTC)
            )

    async def forbidden(*args, **kwargs):
        raise AssertionError("Microsoft credentials must never reach Google or a new transaction")

    class GoogleProvider:
        collect = forbidden

    monkeypatch.setattr(calendar_busy, "get_settings", lambda: env.settings)
    monkeypatch.setattr(calendar_busy, "MicrosoftMailGateway", Gateway, raising=False)
    monkeypatch.setattr(calendar_busy, "MicrosoftAvailabilityProvider", BusyProvider, raising=False)
    monkeypatch.setattr(calendar_busy, "GoogleAvailabilityProvider", GoogleProvider)
    monkeypatch.setattr(calendar_busy, "access_token_for_connection", forbidden)
    monkeypatch.setattr(microsoft, "access", forbidden)
    return env


async def collect(env, database):
    await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
    return await calendar_busy.collect(
        database,
        env.user_id,
        env.workspace_id,
        start_at=START,
        end_at=START.replace(hour=23),
        travel_minutes=15,
    )


@pytest.mark.asyncio
async def test_microsoft_busy_is_used_and_rotation_is_owned_by_parent_transaction(
    microsoft_busy_env, monkeypatch
):
    env = microsoft_busy_env
    async with env.factory() as database:
        original = await calendar_busy.inventory(database, env.user_id, env.workspace_id)
        assert original[0]["provider"] == "microsoft"

        async def forbidden_boundary():
            raise AssertionError("Collector must not commit or roll back its caller")

        with monkeypatch.context() as isolated:
            isolated.setattr(database, "commit", forbidden_boundary)
            isolated.setattr(database, "rollback", forbidden_boundary)
            result = await collect(env, database)
        assert result.connected
        assert result.intervals == (
            (BUSY_START - timedelta(minutes=15), BUSY_END + timedelta(minutes=15)),
        )
        credential = await database.get(ConnectionCredential, env.credential_id)
        assert (
            CredentialVault(env.settings).reveal_refresh_token(credential.encrypted_refresh_token)
            == NEW_REFRESH
        )
        assert NEW_REFRESH not in credential.encrypted_refresh_token
        assert (
            env.microsoft.token_calls
            == env.microsoft.identity_calls
            == env.microsoft.busy_calls
            == 1
        )
        for secret in (OLD_REFRESH, NEW_REFRESH, ACCESS, EMAIL, ACCOUNT):
            assert secret not in repr(result) and secret not in repr(original)
        await database.rollback()
    async with env.factory() as database:
        credential = await database.get(ConnectionCredential, env.credential_id)
        assert (
            CredentialVault(env.settings).reveal_refresh_token(credential.encrypted_refresh_token)
            == OLD_REFRESH
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["partial", "unavailable"])
async def test_microsoft_incomplete_busy_coverage_never_returns_confirmed_plan(
    microsoft_busy_env, state
):
    env = microsoft_busy_env
    env.microsoft.state = state
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError) as raised:
            await collect(env, database)
        assert raised.value.status_code == 409
        assert "fully checked" in str(raised.value)
        assert OLD_REFRESH not in str(raised.value) and EMAIL not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["account", "email", "scopes"])
async def test_microsoft_identity_or_permission_change_prevents_busy_read(
    microsoft_busy_env, change
):
    env = microsoft_busy_env
    if change == "account":
        env.microsoft.account = "wrong-account-private"
    elif change == "email":
        env.microsoft.email = "wrong-account@example.com"
    else:
        env.microsoft.scopes = frozenset({"openid", "User.Read"})
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError):
            await collect(env, database)
        assert env.microsoft.busy_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["revoked", "paused"])
@pytest.mark.parametrize("stage", ["token", "busy"])
async def test_microsoft_provider_failure_rechecks_native_authority_before_generic_error(
    microsoft_busy_env, mutation, stage
):
    env = microsoft_busy_env
    env.microsoft.failure = stage
    async with env.factory() as database:

        async def change():
            if mutation == "revoked":
                grant = await database.get(StudentCalendarGrant, env.grant_id)
                grant.status, grant.revoked_at = "revoked", await database_now(database)
                state = await database.get(StudentCalendarState, env.state_id)
                state.revision += 1
            else:
                user = await schedule.owner(database, env.user_id, env.workspace_id)
                user.agent_paused = True
            await database.flush()

        env.microsoft.during = change
        with pytest.raises(schedule.StudentError) as raised:
            await collect(env, database)
        assert raised.value.status_code == 409
        assert "synthetic-private-provider-body" not in str(raised.value)
        assert OLD_REFRESH not in str(raised.value) and NEW_REFRESH not in str(raised.value)


@pytest.mark.asyncio
async def test_microsoft_refresh_failure_is_explicitly_unknown_and_private(microsoft_busy_env):
    env = microsoft_busy_env
    env.microsoft.failure = "token"
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError) as raised:
            await collect(env, database)
        assert raised.value.status_code == 503
        assert env.microsoft.busy_calls == 0
        assert "synthetic-private-provider-body" not in str(raised.value)
