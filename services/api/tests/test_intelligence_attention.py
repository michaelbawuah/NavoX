from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api.main import app
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import (
    Commitment,
    IntelligenceFeedback,
    IntelligencePreference,
    ProactiveSignal,
)
from navox.db.session import get_database_session
from navox.intelligence.attention import (
    FEATURE_WEIGHTS,
    band_for,
    evaluate_workspace_attention,
    score_commitment,
)
from navox.intelligence.feedback import record_feedback
from navox.proactive.engine import evaluate_workspace
from navox.today.projection import build_today_projection

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


def commitment(**overrides: object) -> Commitment:
    values: dict[str, object] = {
        "id": uuid4(),
        "workspace_id": uuid4(),
        "user_id": uuid4(),
        "commitment_type": "deadline",
        "title": "Send the budget",
        "priority": 5,
        "confidence": 0.97,
        "status": "confirmed",
        "created_by": "ai",
        "due_at": NOW + timedelta(hours=2),
        "dedupe_key": uuid4().hex,
        "created_at": NOW - timedelta(days=3),
        "updated_at": NOW,
        "intelligence_metadata": {},
    }
    values.update(overrides)
    return Commitment(**values)


def test_normalized_formula_stored_breakdown_and_bands() -> None:
    result = score_commitment(
        commitment(), now=NOW, objective_active=True, relationship_importance=0.8
    )
    assert set(result.factors) == set(FEATURE_WEIGHTS)
    assert all(0 <= value <= 1 for value in result.factors.values())
    assert result.raw_score == round(
        100 * sum(FEATURE_WEIGHTS[name] * value for name, value in result.factors.items())
    )
    assert result.band == band_for(result.score)
    assert [band_for(value) for value in (0, 29, 30, 49, 50, 69, 70, 89, 90, 100)] == [
        "SUPPRESS",
        "SUPPRESS",
        "DASHBOARD",
        "DASHBOARD",
        "TODAY",
        "TODAY",
        "TODAY_HIGH",
        "TODAY_HIGH",
        "NOW",
        "NOW",
    ]


def test_confidence_and_duplicates_override_even_extreme_urgency() -> None:
    weak = score_commitment(commitment(confidence=0.69), now=NOW, objective_active=True)
    assert weak.suppressed and weak.score == 0 and weak.suggested_capability is None
    candidate = score_commitment(commitment(confidence=0.85), now=NOW, objective_active=True)
    assert candidate.score <= 69 and candidate.suggested_capability == "commitment.review"
    duplicate = score_commitment(commitment(), now=NOW, duplicate=True)
    assert duplicate.score == 0 and duplicate.suppressed


def test_fatigue_response_window_and_meeting_preparation_overrides() -> None:
    fresh_wait = score_commitment(
        commitment(status="waiting", waiting_since=NOW, due_at=NOW + timedelta(days=3)), now=NOW
    )
    assert fresh_wait.score <= 49 and "Response window is still open" in fresh_wait.reasons
    meeting = score_commitment(commitment(commitment_type="meeting", priority=1), now=NOW)
    assert meeting.score >= 70 and meeting.suggested_capability == "meeting.prepare"
    fatigued = score_commitment(
        commitment(commitment_type="meeting"), now=NOW, notification_fatigue=1
    )
    assert fatigued.score <= 49
    quiet = score_commitment(
        commitment(), now=NOW, objective_active=True, relationship_importance=1, interruption_cost=1
    )
    assert quiet.band != "NOW"


def test_date_windows_remain_uncertain_and_do_not_invent_a_due_at() -> None:
    item = commitment(
        due_at=None,
        intelligence_metadata={
            "temporal": {
                "expression": "tomorrow",
                "start_at": "2026-09-23T00:00:00+00:00",
                "end_at": "2026-09-24T00:00:00+00:00",
            }
        },
    )
    result = score_commitment(item, now=NOW)
    assert item.due_at is None
    assert result.factors["urgency"] > 0
    assert any("exact time is unconfirmed" in reason for reason in result.reasons)
    item.commitment_type = "meeting"
    item.intelligence_metadata = {"temporal": {"end_at": (NOW + timedelta(hours=1)).isoformat()}}
    assert score_commitment(item, now=NOW).suggested_capability != "meeting.prepare"


def test_preferences_and_source_metadata_cannot_escalate_authority() -> None:
    item = commitment(
        intelligence_metadata={
            "permissions": ["gmail.send"],
            "priority": 9999,
            "attention_score": 100,
        }
    )
    baseline = score_commitment(item, now=NOW)
    learned = score_commitment(item, now=NOW, preferences={"deadline": 1000})
    assert learned.score <= baseline.score + 5
    assert learned.suggested_capability == baseline.suggested_capability == "commitment.handle"
    assert item.status == "confirmed"


@pytest_asyncio.fixture
async def intelligence_environment() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, sessions
    app.dependency_overrides.pop(get_database_session, None)
    app.dependency_overrides.pop(get_settings, None)
    await engine.dispose()


async def register(client: AsyncClient, email: str = "reader@example.com") -> tuple[UUID, UUID]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct long password",
            "display_name": "Reader",
        },
    )
    assert response.status_code == 201
    data = response.json()
    return UUID(data["id"]), UUID(data["workspace"]["id"])


@pytest.mark.asyncio
async def test_persisted_attention_and_today_use_same_state_with_suppression(
    intelligence_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = intelligence_environment
    user_id, workspace_id = await register(client)
    items = [
        commitment(user_id=user_id, workspace_id=workspace_id, title="Budget"),
        commitment(user_id=user_id, workspace_id=workspace_id, title="Weak", confidence=0.4),
        commitment(
            user_id=user_id,
            workspace_id=workspace_id,
            title="Duplicate",
            intelligence_metadata={"duplicate_of": str(uuid4())},
        ),
    ]
    async with sessions() as session:
        session.add_all(items)
        await session.flush()
        scores = await evaluate_workspace_attention(
            session, user_id=user_id, workspace_id=workspace_id, now=NOW
        )
        assert items[0].attention_factors == scores[items[0].id].factors
        assert items[0].attention_score == scores[items[0].id].score
        await session.commit()
    today = await client.get("/api/v1/today")
    assert today.status_code == 200
    assert today.headers["cache-control"] == "no-store"
    assert today.json()["total"] == 1
    row = (today.json()["needs_attention"] + today.json()["coming_up"])[0]
    assert row["title"] == "Budget"
    assert set(row["factors"]) == set(FEATURE_WEIGHTS)
    assert row["category"] == "NEEDS_ATTENTION"


@pytest.mark.asyncio
async def test_feedback_is_idempotent_scoped_and_has_no_action_authority(
    intelligence_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = intelligence_environment
    assert (await client.get("/api/v1/intelligence/status")).status_code == 401
    user_id, workspace_id = await register(client)
    item = commitment(user_id=user_id, workspace_id=workspace_id)
    async with sessions() as session:
        session.add(item)
        await session.commit()
    payload = {
        "request_id": str(uuid4()),
        "target_id": str(item.id),
        "target_type": "commitment",
        "feedback_type": "more_like_this",
    }
    first = await client.post("/api/v1/intelligence/feedback", json=payload)
    assert first.status_code == 200
    repeated = await client.post("/api/v1/intelligence/feedback", json=payload)
    assert repeated.status_code == 200 and not repeated.json()["applied"]
    assert first.json()["id"] == repeated.json()["id"]
    conflict = await client.post(
        "/api/v1/intelligence/feedback", json={**payload, "feedback_type": "incorrect"}
    )
    assert conflict.status_code == 409
    escalation = await client.post(
        "/api/v1/intelligence/feedback", json={**payload, "permissions": ["gmail.send"]}
    )
    assert escalation.status_code == 422
    status = await client.get("/api/v1/intelligence/status")
    assert status.json()["feedback_count"] == 1
    async with sessions() as session:
        saved = await session.get(Commitment, item.id)
        assert saved is not None and saved.status == "confirmed"
    await register(client, "other-reader@example.com")
    leak = await client.post("/api/v1/intelligence/feedback", json=payload)
    assert leak.status_code == 404
    other_status = await client.get("/api/v1/intelligence/status")
    assert other_status.json()["feedback_count"] == 0
    assert other_status.json()["active_commitments"] == 0


@pytest.mark.asyncio
async def test_feedback_learning_saturates_and_repeated_clicks_do_not_amplify(
    intelligence_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = intelligence_environment
    user_id, workspace_id = await register(client)
    item = commitment(user_id=user_id, workspace_id=workspace_id)
    async with sessions() as session:
        session.add(item)
        await session.commit()
        for index in range(10):
            await record_feedback(
                session,
                workspace_id=workspace_id,
                user_id=user_id,
                target_id=item.id,
                request_id=uuid4(),
                feedback_type="more_like_this",
                now=NOW + timedelta(days=index),
            )
            await session.flush()
        pref = await session.scalar(select(IntelligencePreference))
        assert pref is not None and pref.weights["deadline"] == 0.05
        await record_feedback(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            target_id=item.id,
            request_id=uuid4(),
            feedback_type="less_like_this",
            now=NOW + timedelta(days=11),
        )
        value = dict(pref.weights)
        _, _, repeated = await record_feedback(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            target_id=item.id,
            request_id=uuid4(),
            feedback_type="less_like_this",
            now=NOW + timedelta(days=11, minutes=1),
        )
        assert repeated == value
        feedback = list(await session.scalars(select(IntelligenceFeedback)))
        assert len(feedback) == 12
        assert item.status == "confirmed"


@pytest.mark.asyncio
async def test_recent_signals_lower_ranking_without_losing_saved_work(
    intelligence_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = intelligence_environment
    user_id, workspace_id = await register(client)
    item = commitment(user_id=user_id, workspace_id=workspace_id, commitment_type="meeting")
    async with sessions() as session:
        session.add(item)
        session.add(
            ProactiveSignal(
                user_id=user_id,
                workspace_id=workspace_id,
                commitment_id=item.id,
                signal_type="meeting_prep",
                fingerprint=uuid4().hex,
                status="active",
                tier="briefing",
                attention_score=70,
                score_components={},
                what_happening="Meeting",
                why_matters="Soon",
                last_surfaced_at=NOW,
                surface_count=3,
            )
        )
        await session.flush()
        scores = await evaluate_workspace_attention(
            session, user_id=user_id, workspace_id=workspace_id, now=NOW
        )
        assert scores[item.id].score <= 49
        assert not scores[item.id].suppressed


@pytest.mark.asyncio
async def test_intelligence_today_and_proactive_share_score_and_fail_closed_on_weak_evidence(
    intelligence_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = intelligence_environment
    user_id, workspace_id = await register(client)
    strong = commitment(
        user_id=user_id,
        workspace_id=workspace_id,
        intelligence_metadata={"resolution": "CREATE_NEW"},
    )
    weak = commitment(
        user_id=user_id,
        workspace_id=workspace_id,
        confidence=0.4,
        intelligence_metadata={"resolution": "CREATE_NEW"},
    )
    duplicate = commitment(
        user_id=user_id,
        workspace_id=workspace_id,
        intelligence_metadata={"duplicate_of": str(strong.id)},
    )
    async with sessions() as session:
        session.add_all([strong, weak, duplicate])
        session.add(
            IntelligencePreference(
                user_id=user_id, workspace_id=workspace_id, weights={"deadline": 0.03}
            )
        )
        await session.commit()
        signals = await evaluate_workspace(
            session, user_id=user_id, workspace_id=workspace_id, timezone_name="UTC", now=NOW
        )
        projection = await build_today_projection(
            session, user_id=user_id, workspace_id=workspace_id, timezone_name="UTC", now=NOW
        )
        rows = [*projection.needs_attention, *projection.coming_up]
        assert len(rows) == 1 and rows[0].id == strong.id
        signal = next(row for row in signals if row.commitment_id == strong.id)
        assert signal.attention_score == rows[0].score
        assert signal.score_components == rows[0].factors
        assert signal.why_matters == "; ".join(rows[0].reasons)
        assert all(
            row.tier == "suppressed"
            for row in signals
            if row.commitment_id in {weak.id, duplicate.id}
        )


@pytest.mark.asyncio
async def test_source_correction_preserves_alert_identity_dismissal_and_fatigue(
    intelligence_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, sessions = intelligence_environment
    user_id, workspace_id = await register(client)
    item = commitment(
        user_id=user_id,
        workspace_id=workspace_id,
        intelligence_metadata={"resolution": "CREATE_NEW"},
    )
    async with sessions() as session:
        session.add(item)
        await session.commit()
        first = await evaluate_workspace(
            session, user_id=user_id, workspace_id=workspace_id, timezone_name="UTC", now=NOW
        )
        signal = first[0]
        signal.surface_count, signal.last_surfaced_at = 3, NOW
        signal.status = "dismissed"
        await session.commit()
        item.due_at = NOW + timedelta(minutes=20)
        await session.commit()
        corrected = await evaluate_workspace(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            timezone_name="UTC",
            now=NOW + timedelta(minutes=1),
        )
        assert len(corrected) == 1
        assert corrected[0].id == signal.id
        assert corrected[0].tier == "suppressed"
        assert corrected[0].score_components["notification_fatigue"] == 1
        assert corrected[0].surface_count == 3
