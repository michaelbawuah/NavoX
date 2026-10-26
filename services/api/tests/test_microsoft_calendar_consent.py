"""Native consent/rotation/revocation tests with synthetic Microsoft responses."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import select
from test_student_calendar_consent import BASE
from test_student_calendar_consent import calendar_env as calendar_env

from navox.core.credential_vault import CredentialVault
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt, User
from navox.db.student_planning import StudentPlanningState
from navox.providers.google_availability import ProviderBusy
from navox.providers.microsoft_availability import MicrosoftAvailabilitySnapshot
from navox.providers.microsoft_mail import MailIdentity, MailToken, MicrosoftMailGateway
from navox.student_calendars import microsoft, service
from navox.student_calendars.schemas import MICROSOFT_AVAILABILITY_SCOPES, CalendarCommand


@pytest_asyncio.fixture
async def microsoft_env(calendar_env, monkeypatch):
    env = calendar_env
    env.settings.microsoft_oauth_client_id = "calendar-client"
    env.settings.microsoft_oauth_client_secret = SecretStr("calendar-client-secret")
    env.exchanges = []
    env.token_scopes = frozenset(microsoft.SCOPES)

    async def token(self, settings, **kwargs):
        if self.check:
            await self.check()
        env.exchanges.append(kwargs)
        if getattr(env, "during_token", None):
            await env.during_token()
        if self.check:
            await self.check()
        return MailToken(
            "synthetic-calendar-access", "synthetic-calendar-refresh", env.token_scopes
        )

    async def identity(self, token):
        if self.check:
            await self.check()
        return MailIdentity("subject", "account-42", "calendar@example.test")

    monkeypatch.setattr(MicrosoftMailGateway, "token", token)
    monkeypatch.setattr(MicrosoftMailGateway, "identity", identity)
    return env


async def begin(env):
    current = (await env.client.get(BASE, headers=env.headers)).json()
    response = await env.client.post(
        BASE + "/outlook/start",
        headers=env.headers,
        json={
            "generation": current["generation"],
            "revision": current["revision"],
            "request_id": str(uuid4()),
            "consent_version": "calendar-availability.v1",
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    return result, state


async def finish(env, state):
    return await env.client.get(
        "/api/v1/outlook/callback", params={"state": state, "code": "synthetic-code"}
    )


@pytest.mark.asyncio
async def test_calendar_consent_never_requests_mail_and_finishes_on_existing_callback(
    microsoft_env,
):
    env = microsoft_env
    result, state = await begin(env)
    assert set(result["requested_scopes"]) == set(microsoft.SCOPES)
    assert not any("Mail." in scope for scope in result["requested_scopes"])
    assert urlsplit(result["authorization_url"]).hostname == "login.microsoftonline.com"
    response = await finish(env, state)
    assert response.status_code == 303, response.text
    view = (await env.client.get(BASE, headers=env.headers)).json()
    assert view["grants"][0]["provider"] == "outlook"
    assert view["grants"][0]["status"] == "connected"
    async with env.factory() as database:
        connection = await database.scalar(
            select(Connection).where(Connection.provider == "microsoft")
        )
        assert connection.external_account_id == "calendar:account-42"
        assert set(connection.granted_scopes) == set(microsoft.SCOPES)
        credential = await database.get(ConnectionCredential, connection.credential_reference)
        assert "synthetic-calendar-refresh" not in credential.encrypted_refresh_token
        assert (
            CredentialVault(env.settings).reveal_refresh_token(credential.encrypted_refresh_token)
            == "synthetic-calendar-refresh"
        )
    replay = await finish(env, state)
    assert replay.status_code == 409
    assert len(env.exchanges) == 1


@pytest.mark.asyncio
async def test_revoking_pending_calendar_fences_callback_before_provider_exchange(microsoft_env):
    env = microsoft_env
    result, state = await begin(env)
    response = await env.client.post(
        BASE + f"/{result['grant_id']}/disconnect",
        headers=env.headers,
        json={
            "generation": result["generation"],
            "revision": result["revision"],
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200
    assert (await finish(env, state)).status_code == 409
    assert env.exchanges == []


@pytest.mark.asyncio
async def test_consent_rechecks_revocation_after_token_before_identity_and_storage(microsoft_env):
    env = microsoft_env
    result, state = await begin(env)

    async def revoke():
        async with env.factory() as database:
            await service.disconnect(
                database,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                UUID(result["grant_id"]),
                CalendarCommand(
                    generation=result["generation"], revision=result["revision"], request_id=uuid4()
                ),
                env.settings,
            )

    env.during_token = revoke
    assert (await finish(env, state)).status_code == 409
    async with env.factory() as database:
        assert (
            await database.scalar(select(Connection.id).where(Connection.provider == "microsoft"))
            is None
        )


@pytest.mark.asyncio
async def test_calendar_scope_supports_rotation_and_keeps_distinct_provider(microsoft_env):
    env = microsoft_env
    _, state = await begin(env)
    assert (await finish(env, state)).status_code == 303
    async with env.factory() as database:
        snapshot = await service.scope_snapshot(
            database, env.user_id, env.workspace_id, env.token_hash
        )
        await database.commit()
    assert snapshot.grants[0].provider == "microsoft"
    assert set(MICROSOFT_AVAILABILITY_SCOPES).issubset(snapshot.grants[0].granted_scopes)
    token = await microsoft.access(
        env.factory, env.settings, snapshot, snapshot.grants[0], env.token_hash
    )
    assert token == "synthetic-calendar-access"
    assert "refresh" in env.exchanges[-1]


@pytest.mark.asyncio
async def test_expired_or_other_browser_callback_cannot_redeem(microsoft_env):
    env = microsoft_env
    _, state = await begin(env)
    async with env.factory() as database:
        attempt = await database.scalar(
            select(OAuthAuthorizationAttempt).where(
                OAuthAuthorizationAttempt.purpose == microsoft.PURPOSE
            )
        )
        attempt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    assert (await finish(env, state)).status_code == 409
    assert env.exchanges == []


@pytest.mark.asyncio
async def test_outlook_configuration_does_not_depend_on_google_oauth(microsoft_env):
    env = microsoft_env
    env.settings.google_oauth_client_id = ""
    env.settings.google_oauth_client_secret = None
    providers = (await env.client.get(BASE, headers=env.headers)).json()["providers"]
    assert providers[0]["availability"] == "setup_pending"
    assert providers[1]["availability"] == "available"


@pytest.mark.asyncio
async def test_narrowed_refresh_permissions_are_retained_and_block_busy_reads(microsoft_env):
    from navox.student.service import StudentError

    env = microsoft_env
    _, state = await begin(env)
    assert (await finish(env, state)).status_code == 303
    async with env.factory() as database:
        snapshot = await service.scope_snapshot(
            database, env.user_id, env.workspace_id, env.token_hash
        )
        await database.commit()
    env.token_scopes = frozenset(microsoft.IDENTITY_SCOPES)
    with pytest.raises(StudentError, match="permissions changed"):
        await microsoft.access(
            env.factory, env.settings, snapshot, snapshot.grants[0], env.token_hash
        )
    async with env.factory() as database:
        connection = await database.get(Connection, snapshot.grants[0].connection_id)
        assert "Calendars.Read" not in connection.granted_scopes


@pytest.mark.asyncio
async def test_connected_outlook_is_used_by_native_availability(microsoft_env, monkeypatch):
    from navox.student_availability import service as availability
    from navox.student_planning.schemas import Preferences

    env = microsoft_env
    _, state = await begin(env)
    assert (await finish(env, state)).status_code == 303
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=7, minute=0, second=0, microsecond=0
    )
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "UTC"
        database.add(
            StudentPlanningState(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                revision=1,
                preferences_revision=1,
                preferences=Preferences().model_dump(),
            )
        )
        await database.commit()
    calls = []

    class Reader:
        async def collect(self, *, access_token, start_at, end_at, check):
            await check()
            assert access_token == "synthetic-calendar-access"
            calls.append((start_at, end_at))
            await check()
            return MicrosoftAvailabilitySnapshot(
                "complete",
                2,
                (ProviderBusy(tomorrow.replace(hour=10), tomorrow.replace(hour=11)),),
                datetime.now(UTC),
            )

    monkeypatch.setattr(availability, "MicrosoftAvailabilityProvider", Reader)
    response = await env.client.post(
        "/api/v1/student/availability/preview",
        headers=env.headers,
        json={
            "start_at": tomorrow.isoformat(),
            "end_at": tomorrow.replace(hour=23).isoformat(),
            "duration_minutes": 30,
            "preferences_revision": 1,
            "expected_timezone": "UTC",
            "routine_confirmed": True,
            "travel_assumption": "zero",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["result"]["status"] == "complete"
    assert len(calls) == 1
    assert result["source_summary"][-1]["source_id"].startswith("microsoft-")
    assert result["source_summary"][-1]["calendar_count"] == 2
    assert result["input"]["busy"][0]["kind"] == "meeting"
