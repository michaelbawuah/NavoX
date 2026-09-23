from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.exceptions import ApplicationError

from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import AuditEvent, Connection, IncomingEvent, User, Workspace
from navox.intelligence import activities, ingestion
from navox.intelligence.jobs import SourceWork
from navox.intelligence.source_cooldown import source_cooldown, source_retry_after
from navox.providers.google_sources import GoogleSourceError

NOW = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def cooldown_env() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], list[Connection]]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as database:
        await database.run_sync(Base.metadata.create_all)
    async with factory() as database:
        connections = []
        for index in range(2):
            user = User(email=f"owner-{index}@example.com")
            workspace = Workspace(name=f"Workspace {index}")
            database.add_all([user, workspace])
            await database.flush()
            connection = Connection(
                user_id=user.id,
                workspace_id=workspace.id,
                provider="google",
                external_account_id=f"google-{index}",
                granted_scopes=[
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/calendar.events.readonly",
                ],
            )
            database.add(connection)
            connections.append(connection)
        await database.commit()
    try:
        yield factory, connections
    finally:
        await engine.dispose()


def failure_audit(
    connection: Connection,
    *,
    occurred_at: datetime = NOW,
    retry_not_before: datetime | None = None,
    source: str = "gmail",
    code: str = "google_rate_limited",
    **overrides,
) -> AuditEvent:
    values = {
        "user_id": connection.user_id,
        "workspace_id": connection.workspace_id,
        "entity_id": connection.id,
        "entity_type": "connection",
        "event_type": "intelligence.source.failed",
        "actor_type": "system",
        "occurred_at": occurred_at,
        "event_metadata": {
            "source": source,
            "error_diagnostic": {"code": code, "http_status": 429},
            "retry_not_before": (
                retry_not_before or occurred_at + timedelta(minutes=5)
            ).isoformat(),
        },
    }
    values.update(overrides)
    return AuditEvent(**values)


@pytest.mark.asyncio
async def test_cooldown_survives_a_new_session_and_expires_without_writes(cooldown_env):
    factory, connections = cooldown_env
    connection = connections[0]
    async with factory() as database:
        database.add(failure_audit(connection))
        await database.commit()

    async with factory() as database:
        assert await source_cooldown(database, connection.id, "gmail", now=NOW) == {
            "code": "google_rate_limited",
            "http_status": 429,
            "retry_after_seconds": 300,
        }
        assert (
            await source_retry_after(
                database, connection.id, "gmail", now=NOW + timedelta(seconds=29)
            )
            == 271
        )
        assert (
            await source_cooldown(database, connection.id, "gmail", now=NOW + timedelta(minutes=5))
            is None
        )
        assert (
            await source_retry_after(
                database, connection.id, "gmail", now=NOW + timedelta(minutes=6)
            )
            is None
        )
        audits = list(await database.scalars(select(AuditEvent)))
        assert len(audits) == 1
        assert (
            audits[0].event_metadata["retry_not_before"] == (NOW + timedelta(minutes=5)).isoformat()
        )


@pytest.mark.asyncio
async def test_cooldown_is_scoped_to_source_connection_and_audit_owner(cooldown_env):
    factory, connections = cooldown_env
    owner, other = connections
    async with factory() as database:
        database.add_all(
            [
                failure_audit(owner, user_id=other.user_id),
                failure_audit(owner, workspace_id=other.workspace_id),
                failure_audit(owner, entity_type="commitment"),
                failure_audit(owner, event_type="intelligence.source.processed"),
                failure_audit(other),
                failure_audit(owner, source="calendar"),
            ]
        )
        await database.commit()
    async with factory() as database:
        assert await source_cooldown(database, owner.id, "gmail", now=NOW) is None
        assert await source_retry_after(database, owner.id, "calendar", now=NOW) == 300
        assert await source_retry_after(database, other.id, "gmail", now=NOW) == 300


@pytest.mark.asyncio
async def test_latest_unexpired_quota_failure_is_not_masked_by_expired_or_unrelated(cooldown_env):
    factory, connections = cooldown_env
    owner = connections[0]
    async with factory() as database:
        database.add_all(
            [
                failure_audit(
                    owner,
                    occurred_at=NOW - timedelta(minutes=4),
                    retry_not_before=NOW + timedelta(minutes=10),
                    code="google_daily_limit_exceeded",
                ),
                failure_audit(
                    owner,
                    occurred_at=NOW - timedelta(minutes=3),
                    retry_not_before=NOW + timedelta(minutes=2),
                    code="google_quota_exceeded",
                ),
                failure_audit(
                    owner,
                    occurred_at=NOW - timedelta(minutes=2),
                    retry_not_before=NOW - timedelta(minutes=1),
                ),
                failure_audit(
                    owner,
                    occurred_at=NOW - timedelta(seconds=30),
                    code="google_permission_denied",
                ),
            ]
        )
        await database.commit()
    async with factory() as database:
        assert await source_cooldown(database, owner.id, "gmail", now=NOW) == {
            "code": "google_quota_exceeded",
            "http_status": 429,
            "retry_after_seconds": 120,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"source": "gmail", "error_diagnostic": None},
        {
            "source": "gmail",
            "error_diagnostic": {"code": ["google_rate_limited"]},
            "retry_not_before": (NOW + timedelta(minutes=5)).isoformat(),
        },
        {
            "source": "gmail",
            "error_diagnostic": {"code": "google_rate_limited"},
            "retry_not_before": "private provider error, not a timestamp",
        },
        {
            "source": "gmail",
            "error_diagnostic": {"code": "google_rate_limited"},
            "retry_not_before": True,
        },
        {
            "source": "gmail",
            "error_diagnostic": {"code": "google_rate_limited"},
            "retry_not_before": (NOW + timedelta(minutes=5)).replace(tzinfo=None).isoformat(),
        },
    ],
)
async def test_malformed_persisted_cooldown_is_ignored(cooldown_env, metadata):
    factory, connections = cooldown_env
    async with factory() as database:
        database.add(failure_audit(connections[0], event_metadata=metadata))
        await database.commit()
    async with factory() as database:
        assert await source_cooldown(database, connections[0].id, "gmail", now=NOW) is None


@pytest.mark.asyncio
async def test_cooldown_does_not_trust_future_audits_or_stale_history(cooldown_env):
    factory, connections = cooldown_env
    async with factory() as database:
        database.add_all(
            [
                failure_audit(connections[0], occurred_at=NOW + timedelta(seconds=1)),
                failure_audit(
                    connections[0],
                    occurred_at=NOW - timedelta(days=2),
                    retry_not_before=NOW + timedelta(minutes=5),
                ),
            ]
        )
        await database.commit()
    async with factory() as database:
        assert await source_cooldown(database, connections[0].id, "gmail", now=NOW) is None


@pytest.mark.asyncio
async def test_cooldown_strips_private_fields_and_bounds_delay(cooldown_env):
    factory, connections = cooldown_env
    async with factory() as database:
        database.add(
            failure_audit(
                connections[0],
                event_metadata={
                    "source": "gmail",
                    "retry_not_before": (NOW + timedelta(days=365)).isoformat(),
                    "error_diagnostic": {
                        "code": "google_daily_limit_exceeded",
                        "http_status": True,
                        "retry_after_seconds": 2**64,
                        "message": "private body and access token",
                    },
                },
            )
        )
        await database.commit()
    async with factory() as database:
        diagnostic = await source_cooldown(database, connections[0].id, "gmail", now=NOW)
        assert diagnostic is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "non_retryable"),
    [
        ("google_rate_limited", False),
        ("google_daily_limit_exceeded", True),
        ("google_quota_exceeded", True),
        ("google_provider_unavailable", False),
    ],
)
async def test_active_cooldown_blocks_provider_and_does_not_extend_it(
    cooldown_env, monkeypatch, code, non_retryable
):
    factory, connections = cooldown_env
    connection = connections[0]
    now = datetime.now(UTC)
    audit = failure_audit(connection, occurred_at=now, code=code)
    deadline = audit.event_metadata["retry_not_before"]
    async with factory() as database:
        database.add(audit)
        await database.commit()

    async def must_not_process(*args, **kwargs):
        pytest.fail("A cooldown must be checked before contacting Google or the model")

    monkeypatch.setattr(ingestion, "process_connection", must_not_process)
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        activities, "get_settings", lambda: Settings(ai_provider="openai", openai_api_key="test")
    )
    payload = SourceWork(
        str(connection.id), str(connection.user_id), str(connection.workspace_id), "gmail"
    )
    for _ in range(2):
        with pytest.raises(ApplicationError) as raised:
            await activities.process_source_activity(payload)
        assert raised.value.type == "IntelligenceProcessingFailure"
        assert raised.value.non_retryable is non_retryable
        diagnostic = raised.value.details[0]
        assert diagnostic["code"] == code
        assert 1 <= diagnostic["retry_after_seconds"] <= 300
        if not non_retryable:
            assert raised.value.next_retry_delay == timedelta(
                seconds=diagnostic["retry_after_seconds"]
            )
    async with factory() as database:
        audits = list(await database.scalars(select(AuditEvent)))
        assert len(audits) == 1
        assert audits[0].event_metadata["retry_not_before"] == deadline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "hint", "expected_delay", "non_retryable"),
    [
        ("google_rate_limited", None, 60, False),
        ("google_rate_limited", 120, 120, False),
        ("google_daily_limit_exceeded", None, 300, True),
        ("google_quota_exceeded", 600, 600, True),
        ("google_provider_unavailable", 120, 120, False),
    ],
)
async def test_new_provider_failure_persists_a_bounded_retry_deadline(
    cooldown_env, monkeypatch, code, hint, expected_delay, non_retryable
):
    factory, connections = cooldown_env
    connection = connections[0]
    http_status = 503 if code == "google_provider_unavailable" else 403
    error = GoogleSourceError(
        "private provider response and token",
        code=code,
        http_status=http_status,
        retry_after_seconds=hint,
    )
    provider_calls = 0

    async def fail(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise error

    monkeypatch.setattr(ingestion, "process_connection", fail)
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        activities, "get_settings", lambda: Settings(ai_provider="openai", openai_api_key="test")
    )
    payload = SourceWork(
        str(connection.id), str(connection.user_id), str(connection.workspace_id), "gmail"
    )
    started = datetime.now(UTC)
    with pytest.raises(ApplicationError) as raised:
        await activities.process_source_activity(payload)
    finished = datetime.now(UTC)
    assert raised.value.type == "IntelligenceProcessingFailure"
    assert raised.value.non_retryable is non_retryable
    if not non_retryable:
        assert raised.value.next_retry_delay == timedelta(seconds=expected_delay)
    assert "private" not in str(raised.value)
    async with factory() as database:
        audit = (await database.scalars(select(AuditEvent))).one()
        assert audit.event_type == "intelligence.source.failed"
        assert audit.event_metadata["source"] == "gmail"
        assert "private" not in str(audit.event_metadata)
        deadline = datetime.fromisoformat(audit.event_metadata["retry_not_before"])
        assert started + timedelta(seconds=expected_delay) <= deadline
        assert deadline <= finished + timedelta(seconds=expected_delay)
        persisted = await source_cooldown(database, connection.id, "gmail", now=finished)
        assert persisted["code"] == code
        assert persisted["http_status"] == http_status
        assert 1 <= persisted["retry_after_seconds"] <= expected_delay
    with pytest.raises(ApplicationError):
        await activities.process_source_activity(payload)
    assert provider_calls == 1
    async with factory() as database:
        unchanged = (await database.scalars(select(AuditEvent))).one()
        assert unchanged.id == audit.id
        assert datetime.fromisoformat(unchanged.event_metadata["retry_not_before"]) == deadline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "preserve_refresh"),
    [
        (GoogleSourceError(code="google_daily_limit_exceeded"), True),
        (GoogleSourceError(code="google_provider_unavailable", retry_after_seconds=86_400), True),
        (GoogleSourceError(code="google_source_error"), False),
        (ValueError("private failure"), False),
    ],
    ids=["quota", "provider-unavailable", "other-provider-error", "general-error"],
)
async def test_backoff_commit_preserves_only_healthy_provider_transaction(
    cooldown_env, monkeypatch, error, preserve_refresh
):
    factory, connections = cooldown_env
    connection = connections[0]
    refreshed_at = datetime.now(UTC)

    async def fail_after_refresh(database, **kwargs):
        current = await database.get(Connection, connection.id)
        current.last_checked_at = refreshed_at
        await database.flush()
        raise error

    monkeypatch.setattr(ingestion, "process_connection", fail_after_refresh)
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        activities, "get_settings", lambda: Settings(ai_provider="openai", openai_api_key="test")
    )
    payload = SourceWork(
        str(connection.id), str(connection.user_id), str(connection.workspace_id), "gmail"
    )
    with pytest.raises(ApplicationError):
        await activities.process_source_activity(payload)
    async with factory() as database:
        current = await database.get(Connection, connection.id)
        assert (current.last_checked_at is not None) is preserve_refresh
        audit = (await database.scalars(select(AuditEvent))).one()
        if preserve_refresh:
            occurred_at = audit.occurred_at.replace(tzinfo=UTC)
            assert occurred_at >= refreshed_at
            deadline = datetime.fromisoformat(audit.event_metadata["retry_not_before"])
            delay = audit.event_metadata["error_diagnostic"]["retry_after_seconds"]
            assert deadline == occurred_at + timedelta(seconds=delay)
            assert await source_cooldown(database, connection.id, "gmail") is not None


@pytest.mark.asyncio
async def test_reconciliation_skips_cooled_inbox_and_watch_but_keeps_other_sources(
    cooldown_env, monkeypatch
):
    factory, connections = cooldown_env
    owner, other = connections
    now = datetime.now(UTC)
    async with factory() as database:
        database.add(failure_audit(owner, occurred_at=now))
        event = IncomingEvent(
            connection_id=owner.id,
            user_id=owner.user_id,
            workspace_id=owner.workspace_id,
            provider="google",
            source="gmail",
            event_type="change",
            external_event_id="pending-during-quota",
            payload_hash="a" * 64,
            received_at=now,
        )
        database.add(event)
        await database.commit()
        event_id = event.id
    watched: list[tuple[UUID, str]] = []

    async def renew_watch(database, *, connection_id, source, settings):
        watched.append((connection_id, source))

    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    monkeypatch.setattr(activities, "get_settings", lambda: Settings(ai_provider="openai"))
    monkeypatch.setattr(ingestion, "renew_source_watch", renew_watch)
    pending = await activities.pending_intelligence_activity()
    expected = {(owner.id, "calendar"), (other.id, "gmail"), (other.id, "calendar")}
    assert {(UUID(item.connection_id), item.source) for item in pending} == expected
    assert set(watched) == expected
    async with factory() as database:
        preserved = await database.get(IncomingEvent, event_id)
        assert preserved.intelligence_status == "pending"
        assert len(list(await database.scalars(select(AuditEvent)))) == 1
