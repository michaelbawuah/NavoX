from collections.abc import AsyncIterator
from dataclasses import asdict
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.exceptions import ApplicationError

from navox.ai.errors import AIProviderError
from navox.api.main import app
from navox.api.workspace import city_weather
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import AuditEvent, Connection, IncomingEvent, User, WorkspaceMembership
from navox.db.session import get_database_session
from navox.intelligence import activities
from navox.intelligence.extraction import InvalidOperationalExtraction
from navox.intelligence.jobs import SourceWork
from navox.intelligence.sync_errors import processing_diagnostic, sanitize_diagnostic
from navox.providers.google_oauth import GoogleAccessTokenError
from navox.providers.google_sources import GoogleSourceAuthorizationError


@pytest_asyncio.fixture
async def runtime_env() -> AsyncIterator[
    tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = sessions
    app.dependency_overrides[get_settings] = lambda: Settings()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Owner",
            },
        )
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_display_preferences_are_validated_persisted_and_private(runtime_env):
    client, _ = runtime_env
    before = (await client.get("/api/v1/workspace/preferences")).json()
    assert before["weather_visible"] is False
    payload = dict(
        before,
        timezone="America/New_York",
        clock_format="24h",
        temperature_unit="fahrenheit",
        weather_visible=True,
        weather_city="Ithaca",
    )
    assert (await client.post("/api/v1/workspace/preferences", json=payload)).json() == payload
    assert (await client.get("/api/v1/workspace/preferences")).json() == payload
    assert (
        await client.post("/api/v1/workspace/preferences", json=dict(payload, timezone="fake/time"))
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/workspace/preferences", json=dict(payload, grant_permission=True)
        )
    ).status_code == 422
    disabled = (
        await client.post(
            "/api/v1/workspace/preferences", json=dict(payload, weather_visible=False)
        )
    ).json()
    assert disabled["weather_city"] is None
    assert (await client.get("/api/v1/workspace/weather")).json()["status"] == "disabled"
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "other@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    assert (await client.get("/api/v1/workspace/preferences")).json() == before


@pytest.mark.asyncio
async def test_manual_sync_is_tenant_and_scope_gated(runtime_env):
    client, factory = runtime_env
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        membership = await db.scalar(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
        )
        connection = Connection(
            user_id=user.id,
            workspace_id=membership.workspace_id,
            provider="google",
            external_account_id="owner-google",
            granted_scopes=["openid"],
        )
        db.add(connection)
        await db.commit()
        connection_id = connection.id
    payload = {"connection_id": str(connection_id), "source": "gmail"}
    assert (await client.post("/api/v1/intelligence/sync", json=payload)).status_code == 403
    assert (
        await client.post(
            "/api/v1/intelligence/sync", json=dict(payload, connection_id=str(uuid4()))
        )
    ).status_code == 404
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "other@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    assert (await client.post("/api/v1/intelligence/sync", json=payload)).status_code == 404


@pytest.mark.asyncio
async def test_city_weather_uses_only_fixed_hosts_and_requested_units():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200, json={"results": [{"name": "Ithaca", "latitude": 42.44, "longitude": -76.5}]}
            )
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["temperature_unit"] == "fahrenheit"
        return httpx.Response(
            200,
            json={
                "current": {"time": "2026-09-22T12:00", "temperature_2m": 72.4, "weather_code": 2}
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await city_weather("Ithaca", "fahrenheit", client)
    assert len(paths) == 2
    assert result.status == "ready"
    assert result.temperature == 72.4
    assert result.observed_at.endswith("Z")


@pytest.mark.asyncio
async def test_city_weather_recovers_from_an_unrecognized_region_suffix():
    geocode_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "geocoding-api.open-meteo.com":
            query = str(request.url.params["name"])
            geocode_queries.append(query)
            if query == "Ithaca, Newyork":
                return httpx.Response(200, json={"results": []})
            assert query == "Ithaca"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Ithaca",
                            "admin1": "New York",
                            "country": "United States",
                            "latitude": 42.44,
                            "longitude": -76.5,
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-09-22T12:00",
                    "temperature_2m": 18.0,
                    "weather_code": 0,
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await city_weather("Ithaca,Newyork", "celsius", client)

    assert geocode_queries == ["Ithaca, Newyork", "Ithaca"]
    assert result.status == "ready"
    assert result.city == "Ithaca, New York, United States"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [
        (
            ValueError("secret source body and credential must not escape"),
            {"code": "intelligence_processing_failed"},
        ),
        (
            GoogleSourceAuthorizationError(
                "secret source body and credential must not escape",
                code="google_api_disabled",
                http_status=403,
            ),
            {"code": "google_api_disabled", "http_status": 403},
        ),
        (
            GoogleAccessTokenError("secret source body and credential must not escape"),
            {"code": "google_token_unavailable"},
        ),
        (
            AIProviderError(
                "secret source body and credential must not escape",
                code="quota_exhausted",
                http_status=429,
            ),
            {
                "code": "provider_request_failed",
                "provider_code": "quota_exhausted",
                "http_status": 429,
            },
        ),
        (
            AIProviderError("secret source body and credential must not escape", code="timeout"),
            {"code": "provider_request_failed", "provider_code": "timeout"},
        ),
        (
            AIProviderError(
                "secret source body and credential must not escape", code="transport_error"
            ),
            {"code": "provider_request_failed", "provider_code": "transport_error"},
        ),
        (
            InvalidOperationalExtraction("secret source body and credential must not escape"),
            {"code": "extraction_validation_failed"},
        ),
    ],
    ids=[
        "unknown",
        "google-source",
        "google-token",
        "ai-provider",
        "ai-timeout",
        "ai-transport",
        "extraction",
    ],
)
async def test_processing_failure_keeps_outbox_and_does_not_leak_source(
    runtime_env, monkeypatch, failure, diagnostic
):
    import navox.intelligence.ingestion as ingestion

    client, factory = runtime_env
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        member = await db.scalar(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
        )
        connection = Connection(
            user_id=user.id,
            workspace_id=member.workspace_id,
            provider="google",
            external_account_id="g",
            granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        db.add(connection)
        await db.flush()
        event = IncomingEvent(
            connection_id=connection.id,
            user_id=user.id,
            workspace_id=member.workspace_id,
            provider="google",
            source="gmail",
            event_type="change",
            external_event_id="e1",
            payload_hash="a" * 64,
        )
        db.add(event)
        await db.commit()
        payload = SourceWork(
            str(connection.id), str(user.id), str(member.workspace_id), "gmail", str(event.id)
        )

    async def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(ingestion, "process_connection", fail)
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        activities,
        "get_settings",
        lambda: Settings(ai_provider="openai", openai_api_key="test-placeholder"),
    )
    with pytest.raises(ApplicationError) as error:
        await activities.process_source_activity(payload)
    assert "secret source" not in str(error.value)
    assert error.value.type == "IntelligenceProcessingFailure"
    assert error.value.details == (diagnostic,)
    assert error.value.non_retryable is False
    async with factory() as db:
        event = await db.get(IncomingEvent, UUID(payload.event_id))
        assert event.intelligence_status == "failed"
        audits = list(
            await db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "intelligence.source.failed")
            )
        )
        assert len(audits) == 1
        assert "secret source" not in str(audits[0].event_metadata)
        assert audits[0].event_metadata["error_type"] == type(failure).__name__
        assert audits[0].event_metadata["error_diagnostic"] == diagnostic
    assert set(asdict(payload)) == {
        "connection_id",
        "user_id",
        "workspace_id",
        "source",
        "event_id",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            {
                "code": "google_scope_missing",
                "http_status": 403,
                "message": "private provider response and token",
                "metadata": {"source": "private mail body"},
            },
            {"code": "google_scope_missing", "http_status": 403},
        ),
        (
            {"code": "private source text", "http_status": True},
            {"code": "intelligence_processing_failed"},
        ),
        (
            {"code": ["google_api_disabled"], "http_status": "403"},
            {"code": "intelligence_processing_failed"},
        ),
        (
            {"code": "google_permission_denied", "http_status": 600},
            {"code": "google_permission_denied"},
        ),
        (
            {"code": "google_permission_denied", "http_status": 99},
            {"code": "google_permission_denied"},
        ),
        ("private provider response", {"code": "intelligence_processing_failed"}),
        (None, {"code": "intelligence_processing_failed"}),
    ],
)
def test_sync_diagnostics_revalidate_persisted_fields(value, expected):
    assert sanitize_diagnostic(value) == expected


def test_unknown_processing_error_cannot_supply_a_diagnostic():
    class UnexpectedError(RuntimeError):
        def diagnostic(self):
            pytest.fail("Arbitrary error diagnostic method must not be invoked")

    assert processing_diagnostic(UnexpectedError("private mail body")) == {
        "code": "intelligence_processing_failed"
    }


@pytest.mark.parametrize("delay", [True, False, 0, -1, 86_401, "60", 60.5, None])
def test_sync_diagnostics_discard_invalid_retry_delays(delay):
    assert sanitize_diagnostic({"code": "google_rate_limited", "retry_after_seconds": delay}) == {
        "code": "google_rate_limited"
    }


@pytest.mark.parametrize("delay", [1, 60, 300, 86_400])
def test_sync_diagnostics_keep_bounded_retry_delays(delay):
    assert sanitize_diagnostic(
        {
            "code": "google_daily_limit_exceeded",
            "http_status": 403,
            "retry_after_seconds": delay,
            "message": "private provider response",
        }
    ) == {
        "code": "google_daily_limit_exceeded",
        "http_status": 403,
        "retry_after_seconds": delay,
    }


def test_google_diagnostic_is_revalidated_at_the_activity_boundary():
    error = GoogleSourceAuthorizationError("private credential")
    # A mutated or future provider implementation cannot add unknown public fields.
    error.code = "private provider message"
    error.http_status = True
    assert processing_diagnostic(error) == {"code": "intelligence_processing_failed"}


@pytest.mark.parametrize("provider_code", ["private provider text", ["timeout"], {}, True, None])
def test_ai_diagnostics_drop_unknown_or_malformed_provider_codes(provider_code):
    assert sanitize_diagnostic(
        {
            "code": "provider_request_failed",
            "provider_code": provider_code,
            "message": "private provider response",
        }
    ) == {"code": "provider_request_failed"}


def test_ai_code_is_not_attached_to_google_failures_or_trusted_after_mutation():
    assert sanitize_diagnostic({"code": "google_rate_limited", "provider_code": "timeout"}) == {
        "code": "google_rate_limited"
    }
    error = AIProviderError("private credential", code="timeout")
    error.code = "private provider message"
    error.http_status = True
    assert processing_diagnostic(error) == {"code": "provider_request_failed"}
