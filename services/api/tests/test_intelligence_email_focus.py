# ruff: noqa: F811
"""Synthetic regressions for a noisy saved inbox, never private mailbox examples."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_today import register, today_environment  # noqa: F401

from navox.db.models import (
    AuditEvent,
    Commitment,
    CommitmentSource,
    IntelligenceFeedback,
    ProactiveSignal,
)
from navox.intelligence.email_focus import email_hold_reason
from navox.proactive.engine import evaluate_workspace, next_meeting_prep, visible_signals

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
RELEVANCE = {
    "intent": "action_required",
    "basis": "direct_request",
    "applies_to_user": True,
    "confidence": 0.99,
}


def email_item(**changes):
    fields = dict(
        id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
        title="Send the design review",
        commitment_type="task",
        status="confirmed",
        priority=3,
        confidence=0.99,
        created_by="ai",
        dedupe_key=uuid4().hex,
        intelligence_metadata={"email_relevance": dict(RELEVANCE)},
        last_verified_at=NOW,
    )
    fields.update(changes)
    return Commitment(**fields)


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"intelligence_metadata": {}}, "Email action has not been verified"),
        ({"title": "Renew"}, "No clear personal action established"),
        ({"status": "candidate"}, "Suggested email action is still uncertain"),
        ({"last_verified_at": NOW - timedelta(days=15)}, "Older email with no current due date"),
        ({"last_verified_at": None}, "Older email with no current due date"),
        ({"last_verified_at": NOW - timedelta(days=13)}, None),
        ({"last_verified_at": NOW - timedelta(days=90), "due_at": NOW - timedelta(days=5)}, None),
        ({"last_verified_at": NOW - timedelta(days=90), "due_at": NOW + timedelta(days=5)}, None),
        (
            {"commitment_type": "meeting", "due_at": NOW - timedelta(days=3)},
            "Email refers to a past meeting",
        ),
        ({"title": "Close the research account"}, None),
    ],
)
def test_freshness_uses_source_time_and_preserves_dated_obligations(changes, expected):
    item = email_item(**changes)
    item.updated_at = NOW  # Rescoring old email cannot make it fresh.
    assert email_hold_reason(item, now=NOW) == expected


async def seed(db, account, **changes):
    item = email_item(
        user_id=UUID(account["id"]), workspace_id=UUID(account["workspace"]["id"]), **changes
    )
    db.add(item)
    await db.flush()
    db.add(
        CommitmentSource(
            commitment_id=item.id,
            provider="google",
            source_type="gmail_message",
            external_resource_id=uuid4().hex,
            source_metadata={},
        )
    )
    await db.commit()
    return item


@pytest.mark.asyncio
async def test_existing_noise_disappears_from_today_queries_and_reminders_without_deletion(
    today_environment,
):
    client, factory = today_environment
    owner = await register(client)
    async with factory() as db:
        legacy = await seed(db, owner, title="Explore seasonal offers", intelligence_metadata={})
        old = await seed(db, owner, last_verified_at=datetime.now(UTC) - timedelta(days=60))
        fresh = await seed(db, owner, last_verified_at=datetime.now(UTC))
        # Old persisted notifications must not bypass the read-time gate.
        signal = ProactiveSignal(
            user_id=legacy.user_id,
            workspace_id=legacy.workspace_id,
            commitment_id=legacy.id,
            signal_type="task_attention",
            fingerprint=uuid4().hex,
            status="active",
            tier="briefing",
            attention_score=80,
            score_components={},
            what_happening="Old suggestion",
            why_matters="Old classification",
            last_evaluated_at=datetime.now(UTC),
        )
        db.add(signal)
        await db.commit()
        assert (
            await visible_signals(db, user_id=legacy.user_id, workspace_id=legacy.workspace_id)
            == []
        )
        await evaluate_workspace(
            db, user_id=legacy.user_id, workspace_id=legacy.workspace_id, timezone_name="UTC"
        )
        assert (await db.get(ProactiveSignal, signal.id)).status == "resolved"
    today = (await client.get("/api/v1/today")).json()
    assert today["total"] == 1
    assert {row["id"] for row in today["set_aside"]} == {str(legacy.id), str(old.id)}
    assert [row["id"] for row in today["coming_up"]] == [str(fresh.id)]
    answer = (
        await client.post("/api/v1/today/query", json={"query": "What do I need to know today?"})
    ).json()
    assert [row["id"] for row in answer["items"]] == [str(fresh.id)]
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 3
        assert await db.scalar(select(func.count()).select_from(CommitmentSource)) == 3
        assert (await db.get(Commitment, legacy.id)).status == "confirmed"


@pytest.mark.asyncio
async def test_keep_is_explicit_idempotent_scoped_and_does_not_restore_terminal_items(
    today_environment,
):
    client, factory = today_environment
    owner = await register(client)
    async with factory() as db:
        item = await seed(db, owner, intelligence_metadata={}, status="candidate", confidence=0.85)
    for _ in range(2):
        kept = await client.post(f"/api/v1/commitments/{item.id}/keep")
        assert kept.status_code == 200
        assert kept.json()["status"] == "confirmed"
    today = (await client.get("/api/v1/today")).json()
    assert today["total"] == 1 and today["set_aside"] == []
    async with factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "commitment.kept")
            )
            == 1
        )
    await client.post(f"/api/v1/commitments/{item.id}/dismiss")
    assert (await client.post(f"/api/v1/commitments/{item.id}/keep")).status_code == 409
    await client.post("/api/v1/auth/logout")
    await register(client, "other@example.com")
    assert (await client.post(f"/api/v1/commitments/{item.id}/keep")).status_code == 404
    today = (await client.get("/api/v1/today")).json()
    assert today["total"] == 0 and today["set_aside"] == []


@pytest.mark.asyncio
async def test_manual_work_and_user_confirmed_email_survive_but_old_ai_confirmation_does_not(
    today_environment,
):
    client, factory = today_environment
    owner = await register(client)
    async with factory() as db:
        manual = await seed(db, owner, created_by="user", intelligence_metadata={})
        chosen = await seed(db, owner, status="candidate", intelligence_metadata={})
        inferred = await seed(db, owner, intelligence_metadata={})
    assert (await client.post(f"/api/v1/commitments/{chosen.id}/confirm")).status_code == 200
    today = (await client.get("/api/v1/today")).json()
    assert {row["id"] for row in today["coming_up"]} == {str(manual.id), str(chosen.id)}
    assert [row["id"] for row in today["set_aside"]] == [str(inferred.id)]


@pytest.mark.asyncio
async def test_next_meeting_does_not_leak_an_unverified_email_invitation(today_environment):
    client, factory = today_environment
    owner = await register(client)
    async with factory() as db:
        invite = await seed(
            db,
            owner,
            commitment_type="meeting",
            due_at=NOW + timedelta(minutes=10),
            intelligence_metadata={},
        )
        real = await seed(
            db, owner, created_by="user", commitment_type="meeting", due_at=NOW + timedelta(hours=2)
        )
        meeting = await next_meeting_prep(
            db, user_id=invite.user_id, workspace_id=invite.workspace_id, now=NOW
        )
        assert meeting.commitment_id == real.id


@pytest.mark.asyncio
async def test_positive_owner_feedback_keeps_legacy_work_in_focus(today_environment):
    client, factory = today_environment
    owner = await register(client)
    async with factory() as db:
        item = await seed(db, owner, intelligence_metadata={})
        feedback = IntelligenceFeedback(
            user_id=item.user_id,
            workspace_id=item.workspace_id,
            target_type="commitment",
            target_id=item.id,
            feedback_type="useful",
            feedback_metadata={},
        )
        db.add(feedback)
        await db.commit()
    today = (await client.get("/api/v1/today")).json()
    assert today["total"] == 1 and today["set_aside"] == []
    async with factory() as db:
        (await db.get(IntelligenceFeedback, feedback.id)).feedback_type = "incorrect"
        await db.commit()
    today = (await client.get("/api/v1/today")).json()
    assert today["total"] == 0 and len(today["set_aside"]) == 1
