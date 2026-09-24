import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_intelligence_evidence import PRIVATE, QUOTE, owned_evidence  # noqa: F401
from test_intelligence_runtime import runtime_env  # noqa: F401

from navox.ai.errors import AIProviderError
from navox.api import gmail_recheck as api
from navox.db.models import (
    AuditEvent,
    Commitment,
    CommitmentSource,
    Connection,
    GmailRecheck,
    IntelligenceCursor,
    IntelligenceFeedback,
    IntelligenceSourceReceipt,
    ObservationEvidence,
    OperationalObservation,
    User,
)
from navox.intelligence.extraction import OperationalExtraction, source_document_hash
from navox.providers.google_sources import GoogleSourceError

ROOT = "/api/v1/intelligence/gmail-recheck"


@pytest_asyncio.fixture
async def legacy(owned_evidence, monkeypatch):  # noqa: F811
    state = owned_evidence
    async with state.factory() as db:
        card = await db.scalar(select(Commitment))
        card.intelligence_metadata = {}
        card.status = (
            "confirmed"  # Historic auto-acceptance is indistinguishable from confirmation.
        )
        state.card_id = card.id
        state.workspace_id = card.workspace_id
        state.card_title = card.title
        connection = await db.get(Connection, state.connection_id)
        connection.external_email = "owner@example.com"
        db.add(
            IntelligenceSourceReceipt(
                connection_id=state.connection_id,
                source="gmail",
                external_id="mail1",
                source_hash=state.extraction.source_hash,
                extractor_version="operational-extraction.v1",
                outcome="processed",
                source_occurred_at=state.document.occurred_at,
                commitment_ids=[str(card.id)],
            )
        )
        db.add(
            IntelligenceCursor(
                connection_id=state.connection_id, source="gmail", cursor="saved-cursor"
            )
        )
        await db.commit()
    state.model = AsyncMock(
        return_value=SimpleNamespace(
            output=OperationalExtraction().model_dump(),
            provider="fixture",
            model="fixture",
        )
    )
    monkeypatch.setattr(
        api, "build_ai_gateway", lambda settings: SimpleNamespace(extract_operational=state.model)
    )
    monkeypatch.setattr(api, "access_token_for_connection", state.token)
    state.payload = {"connection_id": str(state.connection_id), "commitment_id": str(state.card_id)}
    return state


async def preview(state):
    response = await state.client.post(f"{ROOT}/preview", json=state.payload)
    assert response.status_code == 200, response.text
    return response.json()


async def apply(state, item, action="remove"):
    return await state.client.post(
        f"{ROOT}/apply",
        json={
            "connection_id": str(state.connection_id),
            "action": action,
            "items": [{"commitment_id": item["commitment_id"], "preview_id": item["preview_id"]}],
        },
    )


async def duplicate_card(state):
    async with state.factory() as db:
        old = await db.get(Commitment, state.card_id)
        card = Commitment(
            user_id=old.user_id,
            workspace_id=old.workspace_id,
            created_by="ai",
            commitment_type="task",
            title="Other saved item",
            status="confirmed",
            confidence=0.98,
            dedupe_key=str(uuid4()),
            intelligence_metadata={},
        )
        db.add(card)
        await db.flush()
        source = await db.scalar(
            select(CommitmentSource).where(CommitmentSource.commitment_id == old.id)
        )
        db.add(
            CommitmentSource(
                commitment_id=card.id,
                connection_id=source.connection_id,
                provider=source.provider,
                source_type=source.source_type,
                external_resource_id=source.external_resource_id,
                source_metadata=source.source_metadata,
            )
        )
        await db.commit()
        return card.id


@pytest.mark.asyncio
async def test_preview_is_explicit_cached_and_leaves_ingestion_and_cards_unchanged(legacy):
    state = legacy
    page = await state.client.get(ROOT, params={"connection_id": str(state.connection_id)})
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert page.json()["items"][0]["outcome"] == "unchecked"
    state.read.assert_not_awaited()
    state.model.assert_not_awaited()
    result = await preview(state)
    assert result["outcome"] == "remove_suggested"
    assert (await preview(state)) == result
    saved_page = await state.client.get(ROOT, params={"connection_id": str(state.connection_id)})
    assert saved_page.json()["items"] == [result]
    state.model.assert_awaited_once()
    state.read.assert_awaited_once()
    assert state.model.await_args.kwargs["owner_email"] == "owner@example.com"
    assert PRIVATE not in str(result) and QUOTE not in str(result)
    async with state.factory() as db:
        assert (await db.get(Commitment, state.card_id)).status == "confirmed"
        receipt = await db.scalar(select(IntelligenceSourceReceipt))
        assert receipt.extractor_version == "operational-extraction.v1"
        assert receipt.commitment_ids == [str(state.card_id)]
        assert (await db.scalar(select(IntelligenceCursor))).cursor == "saved-cursor"
        assert await db.scalar(select(func.count()).select_from(OperationalObservation)) == 1
        assert await db.scalar(select(func.count()).select_from(ObservationEvidence)) == 1
        saved = await db.get(GmailRecheck, state.card_id)
        assert PRIVATE not in str(saved.__dict__) and QUOTE not in str(saved.__dict__)
        audits = list(await db.scalars(select(AuditEvent)))
        assert PRIVATE not in str([a.event_metadata for a in audits])


@pytest.mark.asyncio
async def test_selected_removal_is_idempotent_and_other_cards_and_evidence_survive(legacy):
    state = legacy
    other_id = await duplicate_card(state)
    result = await preview(state)
    response = await apply(state, result)
    assert response.status_code == 200
    assert response.json() == {"applied": [str(state.card_id)], "skipped": []}
    assert (await apply(state, result)).json() == response.json()
    async with state.factory() as db:
        assert (await db.get(Commitment, state.card_id)).status == "rejected"
        assert (await db.get(Commitment, other_id)).status == "confirmed"
        assert await db.scalar(select(func.count()).select_from(ObservationEvidence)) == 1
        assert await db.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 1
        audits = list(
            await db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "commitment.dismissed")
            )
        )
        assert len(audits) == 1 and audits[0].actor_type == "user"
    state.model.assert_awaited_once()
    state.read.assert_awaited_once()


@pytest.mark.asyncio
async def test_keep_is_durable_and_cannot_be_overridden_by_an_old_removal_preview(legacy):
    state = legacy
    result = await preview(state)
    assert (await apply(state, result, "keep")).json()["applied"] == [str(state.card_id)]
    assert (await apply(state, result, "keep")).json()["applied"] == [str(state.card_id)]
    assert (await apply(state, result)).json()["skipped"] == [str(state.card_id)]
    assert (await preview(state))["reason"] == "user_decision_or_inactive"
    assert (
        await state.client.get(ROOT, params={"connection_id": str(state.connection_id)})
    ).json()["items"] == []
    state.model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["confirmed", "manual", "feedback", "current", "waiting", "terminal"]
)
async def test_existing_user_choices_and_current_policy_items_are_protected(legacy, mode):
    state = legacy
    async with state.factory() as db:
        card = await db.get(Commitment, state.card_id)
        if mode == "confirmed":
            card.status = "candidate"
        elif mode == "manual":
            card.created_by = "user"
        elif mode == "feedback":
            db.add(
                IntelligenceFeedback(
                    user_id=card.user_id,
                    workspace_id=card.workspace_id,
                    target_id=card.id,
                    target_type="commitment",
                    feedback_type="useful",
                )
            )
        elif mode == "current":
            card.intelligence_metadata = {"email_relevance": {"intent": "action_required"}}
        else:
            card.status = "waiting" if mode == "waiting" else "completed"
        await db.commit()
    if mode == "confirmed":
        assert (
            await state.client.post(f"/api/v1/commitments/{state.card_id}/confirm")
        ).status_code == 200
    assert (await preview(state))["outcome"] == "needs_review"
    assert (
        await state.client.get(ROOT, params={"connection_id": str(state.connection_id)})
    ).json()["items"] == []
    state.read.assert_not_awaited()
    state.model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["relevant", "invalid", "model_failure", "changed", "deleted", "missing_locator", "mixed"],
)
async def test_uncertain_or_relevant_sources_never_authorize_removal(legacy, mode):
    state = legacy
    if mode == "relevant":
        state.model.return_value.output = state.extraction.extraction.model_dump()
    elif mode == "invalid":
        state.model.return_value.output = {"observations": [{"object_text": "not grounded"}]}
    elif mode == "model_failure":
        state.model.side_effect = AIProviderError("private-provider-error-text")
    elif mode == "changed":
        state.read.return_value = state.document.model_copy(
            update={"content": "Changed private content"}
        )
    elif mode == "deleted":
        state.read.return_value = state.document.model_copy(
            update={"metadata": {"status": "deleted"}}
        )
    elif mode == "missing_locator":
        async with state.factory() as db:
            (await db.get(ObservationEvidence, state.evidence_id)).evidence_locator = None
            await db.commit()
    else:
        async with state.factory() as db:
            db.add(
                CommitmentSource(
                    commitment_id=state.card_id,
                    provider="navox",
                    source_type="manual",
                    source_metadata={},
                )
            )
            await db.commit()
    result = await preview(state)
    assert result["outcome"] in {"retained", "needs_review", "failed"}
    assert "private-provider-error-text" not in str(result)
    if result["preview_id"]:
        assert (await apply(state, result)).json()["skipped"] == [str(state.card_id)]
    if mode in {"changed", "deleted", "missing_locator", "mixed"}:
        state.model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["title", "terminal", "feedback", "source", "expired", "preview_id"]
)
async def test_stale_or_tampered_previews_cannot_remove_a_card(legacy, mode):
    state = legacy
    result = await preview(state)
    async with state.factory() as db:
        card = await db.get(Commitment, state.card_id)
        if mode == "title":
            card.title = "Owner changed this item"
        elif mode == "terminal":
            card.status = "completed"
        elif mode == "feedback":
            db.add(
                IntelligenceFeedback(
                    user_id=card.user_id,
                    workspace_id=card.workspace_id,
                    target_id=card.id,
                    target_type="commitment",
                    feedback_type="useful",
                )
            )
        elif mode == "source":
            (await db.get(ObservationEvidence, state.evidence_id)).source_hash = "f" * 64
        elif mode == "expired":
            (await db.get(GmailRecheck, card.id)).expires_at = datetime.now(UTC) - timedelta(
                seconds=1
            )
        else:
            result["preview_id"] = str(uuid4())
        await db.commit()
    assert (await apply(state, result)).json() == {"applied": [], "skipped": [str(state.card_id)]}
    async with state.factory() as db:
        assert (await db.get(Commitment, state.card_id)).status != "rejected"


@pytest.mark.asyncio
async def test_owner_isolation_and_forged_selection_are_rejected(legacy):
    state = legacy
    result = await preview(state)
    forged = await state.client.post(
        f"{ROOT}/apply",
        json={
            "connection_id": str(state.connection_id),
            "action": "remove",
            "items": [
                {
                    "commitment_id": str(state.card_id),
                    "preview_id": result["preview_id"],
                    "outcome": "remove_suggested",
                }
            ],
        },
    )
    assert forged.status_code == 422
    await state.client.post(
        "/api/v1/auth/register",
        json={
            "email": "other@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    assert (
        await state.client.get(ROOT, params={"connection_id": str(state.connection_id)})
    ).status_code == 404
    assert (await state.client.post(f"{ROOT}/preview", json=state.payload)).status_code == 404
    assert (await apply(state, result)).status_code == 404
    state.model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["before", "token", "read", "model"])
async def test_pause_and_narrowed_grants_block_reads_results_and_apply(legacy, point):
    state = legacy

    async def revoke():
        async with state.factory() as db:
            (await db.get(Connection, state.connection_id)).granted_scopes = []
            await db.commit()

    async def token(database, **kwargs):
        connection = kwargs["connection"]
        connection.granted_scopes = []
        return "private-token"

    async def read(*args, **kwargs):
        await revoke()
        return state.document

    async def model(*args, **kwargs):
        async with state.factory() as db:
            (await db.get(User, state.user_id)).agent_paused = True
            await db.commit()
        return state.model.return_value

    if point == "before":
        await revoke()
    elif point == "token":
        state.token.side_effect = token
    elif point == "read":
        state.read.side_effect = read
    else:
        state.model.side_effect = model
    response = await state.client.post(f"{ROOT}/preview", json=state.payload)
    assert response.status_code == 403
    if point in {"before", "token"}:
        state.read.assert_not_awaited()
    if point != "model":
        state.model.assert_not_awaited()
    async with state.factory() as db:
        assert (await db.get(Commitment, state.card_id)).status == "confirmed"


@pytest.mark.asyncio
async def test_an_in_progress_preview_is_not_started_twice(legacy):
    state = legacy

    async def while_running(*args, **kwargs):
        repeat = await state.client.post(f"{ROOT}/preview", json=state.payload)
        assert repeat.status_code == 200 and repeat.json()["outcome"] == "checking"
        return state.model.return_value

    state.model.side_effect = while_running
    assert (await preview(state))["outcome"] == "remove_suggested"
    state.model.assert_awaited_once()
    state.read.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_claim_can_resume_but_the_older_request_cannot_overwrite_it(legacy):
    state = legacy
    replacement = None

    async def replace_claim(*args, **kwargs):
        nonlocal replacement
        if state.model.await_count == 1:
            async with state.factory() as db:
                saved = await db.get(GmailRecheck, state.card_id)
                saved.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await db.commit()
            replacement = await preview(state)
        return state.model.return_value

    state.model.side_effect = replace_claim
    older = await state.client.post(f"{ROOT}/preview", json=state.payload)
    assert older.status_code == 409
    assert replacement is not None and replacement["outcome"] == "remove_suggested"
    page = (await state.client.get(ROOT, params={"connection_id": str(state.connection_id)})).json()
    assert page["items"][0]["preview_id"] == replacement["preview_id"]
    assert state.model.await_count == 2


@pytest.mark.asyncio
async def test_a_changed_item_during_model_io_does_not_receive_a_removal_suggestion(legacy):
    state = legacy

    async def edit(*args, **kwargs):
        async with state.factory() as db:
            card = await db.get(Commitment, state.card_id)
            card.title = "A newly revised obligation"
            await db.commit()
        return state.model.return_value

    state.model.side_effect = edit
    result = await preview(state)
    assert result["outcome"] == "needs_review" and result["reason"] == "item_changed"
    assert (await apply(state, result)).json()["applied"] == []


@pytest.mark.asyncio
async def test_a_foreign_item_in_a_batch_does_not_partially_apply_the_owned_selection(legacy):
    state = legacy
    result = await preview(state)
    response = await state.client.post(
        f"{ROOT}/apply",
        json={
            "connection_id": str(state.connection_id),
            "action": "remove",
            "items": [
                {"commitment_id": str(state.card_id), "preview_id": result["preview_id"]},
                {"commitment_id": str(uuid4()), "preview_id": str(uuid4())},
            ],
        },
    )
    assert response.status_code == 404
    async with state.factory() as db:
        assert (await db.get(Commitment, state.card_id)).status == "confirmed"


@pytest.mark.asyncio
async def test_duplicate_support_for_one_message_is_read_and_classified_once(legacy):
    state = legacy
    async with state.factory() as db:
        source = await db.scalar(select(CommitmentSource))
        db.add(
            CommitmentSource(
                commitment_id=source.commitment_id,
                connection_id=source.connection_id,
                provider=source.provider,
                source_type=source.source_type,
                external_resource_id=source.external_resource_id,
                source_metadata=source.source_metadata,
            )
        )
        await db.commit()
    result = await preview(state)
    assert result["outcome"] == "remove_suggested"
    assert result["evidence_ids"] == [str(state.evidence_id)]
    state.read.assert_awaited_once()
    state.model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["all_irrelevant", "later_relevant", "too_many"])
async def test_all_current_support_is_checked_with_a_three_message_limit(legacy, mode):
    state = legacy
    documents = {state.document.external_id: state.document}
    async with state.factory() as db:
        for index in range(2 if mode != "too_many" else 3):
            document = state.document.model_copy(update={"external_id": f"mail{index + 2}"})
            documents[document.external_id] = document
            digest = source_document_hash(document)
            observation = OperationalObservation(
                workspace_id=state.workspace_id,
                user_id=state.user_id,
                observation_type="request",
                status="ACTIVE",
                confidence=0.98,
                extractor_version="operational-extraction.v1",
            )
            db.add(observation)
            await db.flush()
            db.add(
                ObservationEvidence(
                    observation_id=observation.id,
                    connection_id=state.connection_id,
                    provider="google",
                    source_type="gmail_message",
                    external_resource_id=document.external_id,
                    source_hash=digest,
                    observed_at=document.occurred_at,
                    evidence_locator={
                        "spans": [{"source": "content", "start_char": 0, "end_char": len(QUOTE)}]
                    },
                )
            )
            db.add(
                CommitmentSource(
                    commitment_id=state.card_id,
                    connection_id=state.connection_id,
                    provider="google",
                    source_type="gmail_message",
                    external_resource_id=document.external_id,
                    source_metadata={"observation_id": str(observation.id), "source_hash": digest},
                )
            )
        await db.commit()

    async def read(*args, **kwargs):
        return documents[kwargs["external_id"]]

    async def model(document, **kwargs):
        if mode == "later_relevant" and document.external_id == "mail2":
            return SimpleNamespace(
                output=state.extraction.extraction.model_dump(), provider="fixture", model="fixture"
            )
        return state.model.return_value

    state.read.side_effect = read
    state.model.side_effect = model
    result = await preview(state)
    if mode == "too_many":
        assert result["reason"] == "too_many_sources"
        state.read.assert_not_awaited()
        state.model.assert_not_awaited()
    elif mode == "later_relevant":
        assert result["outcome"] == "retained" and state.model.await_count == 2
    else:
        assert result["outcome"] == "remove_suggested" and state.model.await_count == 3


@pytest.mark.asyncio
async def test_check_timeout_cancels_io_and_keeps_the_card(legacy, monkeypatch):
    state = legacy
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    original_timeout = asyncio.timeout
    monkeypatch.setattr(api.asyncio, "timeout", lambda delay: original_timeout(0.1))
    state.model.side_effect = stalled
    result = await preview(state)
    assert result["outcome"] == "failed" and result["reason"] == "timeout"
    assert cancelled.is_set()
    assert (await apply(state, result)).json()["applied"] == []


@pytest.mark.asyncio
async def test_rate_limit_persists_cooldown_and_stops_another_card(legacy):
    state = legacy
    other_id = await duplicate_card(state)
    state.read.side_effect = GoogleSourceError(
        "private Google response",
        code="google_rate_limited",
        http_status=429,
        retry_after_seconds=60,
    )
    result = await preview(state)
    assert result["outcome"] == "failed"
    blocked = await state.client.post(
        f"{ROOT}/preview", json={**state.payload, "commitment_id": str(other_id)}
    )
    assert blocked.status_code == 429
    state.read.assert_awaited_once()
    state.model.assert_not_awaited()


@pytest.mark.asyncio
async def test_pagination_is_stable_when_earlier_items_are_removed(legacy, monkeypatch):
    state = legacy
    await duplicate_card(state)
    await duplicate_card(state)
    monkeypatch.setattr(api, "PAGE_SIZE", 1)
    seen = []
    cursor = None
    while True:
        params = {"connection_id": str(state.connection_id)}
        if cursor:
            params["after"] = cursor
        page = (await state.client.get(ROOT, params=params)).json()
        seen.extend(item["commitment_id"] for item in page["items"])
        cursor = page["next_after"]
        if not cursor:
            break
    assert len(set(seen)) == len(seen) == 3
    state.read.assert_not_awaited()
