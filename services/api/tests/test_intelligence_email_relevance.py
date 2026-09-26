# Imported pytest fixtures are injected by name into the test functions.
# ruff: noqa: F811

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from test_intelligence_extraction import ProposalGateway, source_document, valid_output
from test_intelligence_resolution import document, extraction, resolution_db  # noqa: F401

from navox.ai.gateway import AIGateway, minimized_source_payload
from navox.db.models import (
    Commitment,
    IntelligenceSourceReceipt,
    ObservationEvidence,
    OperationalObservation,
    Person,
    PersonIdentity,
    User,
)
from navox.evaluation.intelligence_smoke import (
    EMAIL_TRIAGE_CASES,
    OfflineSmokeProvider,
    main,
    run_smoke,
)
from navox.intelligence.email_relevance import filter_email_extraction
from navox.intelligence.extraction import (
    EmailRelevance,
    EvidenceSpan,
    InvalidOperationalExtraction,
    OperationalExtractor,
    PersonMention,
    source_document_hash,
)
from navox.intelligence.ingestion import _process_document
from navox.intelligence.resolution import resolve_extraction
from navox.providers.google_sources import GMAIL_READ_SCOPE
from navox.today.projection import build_today_projection


def assessed(
    doc,
    *,
    intent="action_required",
    basis="direct_request",
    applies=True,
    relevance_confidence=0.99,
    kind="request",
):
    result = extraction(doc, obj=doc.content, action="Review", kind=kind, confidence=0.99)
    candidate = result.extraction.observations[0].model_copy(
        update={
            "email_relevance": EmailRelevance(
                intent=intent,
                basis=basis,
                applies_to_user=applies,
                confidence=relevance_confidence,
            ),
        }
    )
    return replace(
        result, extraction=result.extraction.model_copy(update={"observations": [candidate]})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"intent": "no_action", "basis": "promotion"},
        {"intent": "no_action", "basis": "newsletter"},
        {"intent": "no_action", "basis": "optional_invitation"},
        {"intent": "no_action", "basis": "routine_update"},
        {"basis": "unclear"},
        {"basis": "promotion"},
        {"applies": False},
        {"relevance_confidence": 0.89},
        {"intent": "important_alert", "basis": "routine_update", "kind": "alert"},
        {"intent": "important_alert", "basis": "payment_problem", "kind": "request"},
        {"intent": "reply_required", "kind": "completion"},
        {"intent": "commitment_update", "basis": "commitment_progress", "kind": "request"},
    ],
)
async def test_irrelevant_or_inconsistent_high_confidence_mail_creates_no_records(
    resolution_db, changes
):
    db, connection = resolution_db
    doc = document(connection, "Maya mentions the budget in this informational update.")
    result = assessed(doc, **changes)
    person = PersonMention(
        name="Maya", confidence=0.99, evidence=result.extraction.observations[0].evidence
    )
    result = replace(result, extraction=result.extraction.model_copy(update={"people": [person]}))
    assert await resolve_extraction(db, connection=connection, document=doc, result=result) == []
    for table in (Commitment, ObservationEvidence, OperationalObservation, Person, PersonIdentity):
        assert await db.scalar(select(func.count()).select_from(table)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["SPAM", "TRASH", "DRAFT"])
async def test_excluded_mail_labels_cannot_create_tasks(resolution_db, label):
    db, connection = resolution_db
    doc = document(connection, "Please send the budget.", metadata={"label_ids": [label]})
    assert (
        await resolve_extraction(db, connection=connection, document=doc, result=assessed(doc))
        == []
    )


@pytest.mark.asyncio
async def test_sent_question_does_not_become_a_reply_the_user_owes(resolution_db):
    db, connection = resolution_db
    doc = document(
        connection, "Could you send the budget?", outgoing=True, author="owner@example.com"
    )
    assert (
        await resolve_extraction(
            db,
            connection=connection,
            document=doc,
            result=assessed(doc, intent="reply_required"),
        )
        == []
    )


@pytest.mark.asyncio
async def test_quoted_old_request_cannot_be_revived_by_a_wrong_model_assessment(resolution_db):
    db, connection = resolution_db
    quote = "Please send the budget."
    doc = document(connection, f"Thanks, already received.\n\nOn Monday Maya wrote:\n> {quote}")
    result = assessed(doc)
    start = doc.content.index(quote)
    candidate = result.extraction.observations[0].model_copy(
        update={
            "object_text": "budget",
            "evidence": [
                EvidenceSpan(
                    source="content", start_char=start, end_char=start + len(quote), text=quote
                )
            ],
        }
    )
    result = replace(
        result, extraction=result.extraction.model_copy(update={"observations": [candidate]})
    )
    assert await resolve_extraction(db, connection=connection, document=doc, result=result) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent,basis,kind,bulk",
    [
        ("reply_required", "direct_request", "request", False),
        ("action_required", "assigned_obligation", "task", True),
        ("important_alert", "payment_problem", "alert", True),
        ("important_alert", "security_risk", "alert", False),
        ("important_alert", "service_disruption", "alert", False),
        ("important_alert", "schedule_change", "alert", True),
    ],
)
async def test_clear_user_actions_and_alerts_surface_with_the_correct_reason(
    resolution_db, intent, basis, kind, bulk
):
    db, connection = resolution_db
    doc = document(
        connection, "The budget requires your attention.", metadata={"list_unsubscribe": bulk}
    )
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=doc,
        result=assessed(doc, intent=intent, basis=basis, kind=kind),
    )
    assert len(ids) == 1
    task = await db.get(Commitment, ids[0])
    assert task.status == "confirmed"
    assert task.intelligence_metadata["email_relevance"]["intent"] == intent
    today = await build_today_projection(
        db, user_id=connection.user_id, workspace_id=connection.workspace_id, timezone_name="UTC"
    )
    item = (today.needs_attention + today.coming_up)[0]
    if kind == "alert":
        assert item.type == "alert"
        assert item in today.needs_attention
        assert "Important email alert" in item.reasons
    elif intent == "reply_required":
        assert "Reply requested in email" in item.reasons


@pytest.mark.asyncio
async def test_bulk_hint_cannot_hide_an_unproven_subject_only_obligation(resolution_db):
    db, connection = resolution_db
    doc = document(connection, "Please review the budget.", metadata={"list_unsubscribe": True})
    doc = doc.model_copy(update={"subject": doc.content})
    result = assessed(doc, basis="assigned_obligation")
    candidate = result.extraction.observations[0].model_copy(
        update={
            "evidence": [
                EvidenceSpan(
                    source="subject", start_char=0, end_char=len(doc.subject), text=doc.subject
                )
            ]
        }
    )
    result = replace(
        result, extraction=result.extraction.model_copy(update={"observations": [candidate]})
    )
    assert await resolve_extraction(db, connection=connection, document=doc, result=result) == []


@pytest.mark.asyncio
async def test_mixed_email_keeps_only_the_relevant_observation(resolution_db):
    db, connection = resolution_db
    doc = document(connection, "Please send the budget. You can also try our optional upgrade.")
    good = assessed(doc).extraction.observations[0]
    irrelevant = assessed(doc, intent="no_action", basis="promotion").extraction.observations[0]
    result = replace(
        assessed(doc),
        extraction=assessed(doc).extraction.model_copy(update={"observations": [irrelevant, good]}),
    )
    assert (
        len(await resolve_extraction(db, connection=connection, document=doc, result=result)) == 1
    )
    assert await db.scalar(select(func.count()).select_from(OperationalObservation)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,obj",
    [
        ("Unlock", "bonus travel rewards"),
        ("Enter", "the seasonal giveaway"),
        ("Expires", "the discount voucher"),
        ("Use", "the verification code"),
        ("Join", None),
        ("Do", None),
    ],
)
async def test_non_task_shapes_are_ignored_even_when_model_mislabels_them(
    resolution_db, action, obj
):
    db, connection = resolution_db
    doc = document(connection, " ".join(part for part in (action, obj) if part))
    result = extraction(doc, action=action, obj=obj, kind="task", confidence=0.99)
    assert result.extraction.observations[0].email_relevance.applies_to_user
    assert await resolve_extraction(db, connection=connection, document=doc, result=result) == []
    assert await db.scalar(select(func.count()).select_from(Commitment)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,obj",
    [
        ("Return", "the faulty keyboard"),
        ("Close", "the unused research account"),
        ("Submit", "your missing application document"),
        ("Review", "the giveaway compliance report"),
    ],
)
async def test_specific_assigned_actions_survive_non_task_guards(resolution_db, action, obj):
    db, connection = resolution_db
    doc = document(connection, f"Please {action.lower()} {obj} as assigned.")
    result = extraction(doc, action=action, obj=obj, kind="task", confidence=0.99)
    assert (
        len(await resolve_extraction(db, connection=connection, document=doc, result=result)) == 1
    )


@pytest.mark.asyncio
async def test_missing_assessment_fails_closed_but_calendar_contract_remains_valid():
    output = valid_output()
    output["observations"][0].pop("email_relevance")
    extractor = OperationalExtractor(ProposalGateway(output))
    with pytest.raises(InvalidOperationalExtraction) as caught:
        await extractor.extract(source_document())
    assert caught.value.diagnostic() == {"code": "email_relevance_missing"}
    calendar = source_document().model_copy(update={"source_type": "calendar_event"})
    result = await extractor.extract(calendar)
    assert filter_email_extraction(result.extraction, calendar).observations


@pytest.mark.asyncio
async def test_ignored_revision_is_receipted_once_without_repeated_model_calls(resolution_db):
    db, connection = resolution_db
    connection.granted_scopes = [GMAIL_READ_SCOPE]
    await db.flush()
    doc = document(connection, "Read our budget newsletter whenever you like.")
    output = assessed(doc, intent="no_action", basis="newsletter").extraction.model_dump()
    calls = []

    class Gateway(ProposalGateway):
        async def extract_operational(self, document, *, owner_email=None):
            calls.append(owner_email)
            return await super().extract_operational(document, owner_email=owner_email)

    extractor = OperationalExtractor(Gateway(output))
    first = await _process_document(
        db,
        connection=connection,
        user=await db.get(User, connection.user_id),
        source="gmail",
        document=doc,
        extractor=extractor,
    )
    await db.commit()
    second = await _process_document(
        db,
        connection=connection,
        user=await db.get(User, connection.user_id),
        source="gmail",
        document=doc,
        extractor=extractor,
    )
    assert first == (set(), "processed") and second == (set(), "skipped")
    assert calls == ["owner@example.com"]
    assert await db.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 1
    assert await db.scalar(select(func.count()).select_from(OperationalObservation)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["processed", "rejected"])
async def test_contract_upgrade_does_not_replay_old_receipts(resolution_db, outcome):
    db, connection = resolution_db
    doc = document(connection, "Please send the budget.")
    db.add(
        IntelligenceSourceReceipt(
            connection_id=connection.id,
            source="gmail",
            external_id=doc.external_id,
            source_hash=source_document_hash(doc),
            extractor_version="operational-extraction.v1",
            outcome=outcome,
            source_occurred_at=doc.occurred_at,
            commitment_ids=[],
        )
    )
    await db.commit()

    class NoModel:
        async def extract_operational(self, document, *, owner_email=None):
            raise AssertionError("An extraction upgrade must not re-read completed private mail")

    assert await _process_document(
        db,
        connection=connection,
        user=await db.get(User, connection.user_id),
        source="gmail",
        document=doc,
        extractor=OperationalExtractor(NoModel()),
    ) == (set(), "skipped")


def test_model_receives_minimal_owner_and_bulk_context_without_raw_connector_metadata():
    doc = source_document().model_copy(
        update={
            "metadata": {
                "list_unsubscribe": True,
                "label_ids": ["SENT"],
                "private_token": "not-for-model",
            }
        }
    )
    payload = json.loads(minimized_source_payload(doc, owner_email="owner@example.com"))
    assert payload["email_context"] == {
        "owner_email": "owner@example.com",
        "sent": True,
        "bulk_mail_hint": True,
    }
    assert "not-for-model" not in json.dumps(payload)


def test_email_triage_suite_offline_is_bounded_and_keeps_core_suite_separate(monkeypatch, capsys):
    settings = Mock(side_effect=AssertionError("No private settings in fixture mode"))
    monkeypatch.setattr("navox.evaluation.intelligence_smoke.Settings", settings)
    assert main(["--offline", "--suite", "email-triage"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["planned_cases"] == report["passed_cases"] == 24
    assert report["dataset"] == "navox-email-triage-smoke-v2"
    assert report["production_quality_measured"] is False
    assert all(case.content not in json.dumps(report) for case in EMAIL_TRIAGE_CASES)
    assert main(["--suite", "email-triage", "--case", "failed-payment"]) == 0
    assert json.loads(capsys.readouterr().out)["case_ids"] == ["failed-payment"]
    settings.assert_not_called()


@pytest.mark.asyncio
async def test_email_triage_smoke_detects_false_positive_and_missing_required_reply():
    class WrongProvider(OfflineSmokeProvider):
        async def generate_json(self, **kwargs):
            output = await super().generate_json(**kwargs)
            payload = dict(output.data)
            payload["observations"] = []
            return replace(output, data=payload)

    report = await run_smoke(
        AIGateway(WrongProvider()), mode="offline_fixture", cases=EMAIL_TRIAGE_CASES[:1]
    )
    assert report["passed"] is False
    assert report["failed_case_ids"] == ["reply-needed"]

    class FalsePositiveProvider(OfflineSmokeProvider):
        async def generate_json(self, **kwargs):
            output = await super().generate_json(**kwargs)
            source = json.loads(kwargs["input_text"])
            content = source["content"]
            payload = dict(output.data)
            payload["observations"] = [
                {
                    "observation_type": "task",
                    "action_text": "Review",
                    "object_text": content,
                    "confidence": 0.99,
                    "email_relevance": {
                        "intent": "action_required",
                        "basis": "assigned_obligation",
                        "applies_to_user": True,
                        "confidence": 0.99,
                    },
                    "evidence": [
                        {
                            "source": "content",
                            "text": content,
                            "start_char": 0,
                            "end_char": len(content),
                        }
                    ],
                }
            ]
            return replace(output, data=payload)

    negative = next(case for case in EMAIL_TRIAGE_CASES if case.id == "promotional-upgrade")
    report = await run_smoke(
        AIGateway(FalsePositiveProvider()), mode="offline_fixture", cases=(negative,)
    )
    assert report["passed"] is False
    assert report["failed_case_ids"] == ["promotional-upgrade"]
