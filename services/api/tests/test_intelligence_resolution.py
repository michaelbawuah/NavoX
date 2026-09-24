from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from navox.db.base import Base
from navox.db.models import (
    Commitment,
    CommitmentRelation,
    CommitmentSource,
    Connection,
    ObservationEvidence,
    OperationalObservation,
    Person,
    PersonIdentity,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.intelligence.extraction import (
    EvidenceSpan,
    OperationalExtraction,
    OperationalExtractionResult,
    OperationalObservationCandidate,
    source_document_hash,
)
from navox.intelligence.resolution import resolve_extraction, resolve_identity
from navox.intelligence.state import reevaluate_commitment
from navox.intelligence.temporal import resolve_temporal
from navox.providers.google_sources import gmail_document

NOW = datetime(2026, 9, 22, 15, tzinfo=UTC)


@pytest_asyncio.fixture
async def resolution_db() -> AsyncIterator[tuple[AsyncSession, Connection]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        user = User(id=uuid4(), email="owner@example.com", timezone="America/New_York")
        workspace = Workspace(id=uuid4(), name="Personal")
        db.add_all([user, workspace])
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="owner"))
        connection = Connection(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            provider="google",
            external_account_id="google-owner",
            external_email=user.email,
            status="active",
            granted_scopes=["openid"],
        )
        db.add(connection)
        await db.commit()
        yield db, connection
    await engine.dispose()


def document(
    connection: Connection,
    text: str,
    *,
    external_id: str = "mail-1",
    author: str = "manager@example.com",
    outgoing: bool = False,
    occurred_at: datetime = NOW,
    source_type: str = "gmail_message",
    metadata: dict | None = None,
) -> SourceDocument:
    return SourceDocument(
        id=uuid4(),
        workspace_id=connection.workspace_id,
        provider="google",
        source_type=source_type,
        external_id=external_id,
        external_parent_id="thread-1",
        author=SourceIdentity(identity_type="email", identity_value=author),
        recipients=[
            SourceIdentity(
                identity_type="email",
                identity_value=("manager@example.com" if outgoing else "owner@example.com"),
            )
        ],
        subject="Budget approval",
        content=text,
        occurred_at=occurred_at,
        retrieved_at=occurred_at + timedelta(minutes=1),
        metadata=metadata or {"label_ids": ["SENT"] if outgoing else ["INBOX"]},
    )


def extraction(
    doc: SourceDocument,
    *,
    action: str = "send",
    obj: str = "budget",
    kind: str = "request",
    confidence: float = 0.97,
    temporal: str | None = None,
    empty: bool = False,
) -> OperationalExtractionResult:
    assert doc.content is not None
    facts = (
        []
        if empty
        else [
            OperationalObservationCandidate.model_validate(
                {
                    "observation_type": kind,
                    "action_text": action,
                    "object_text": obj,
                    "confidence": confidence,
                    "email_relevance": {
                        "intent": "commitment_update"
                        if kind in {"completion", "waiting"}
                        else "action_required",
                        "basis": "commitment_progress"
                        if kind in {"completion", "waiting"}
                        else "direct_request",
                        "applies_to_user": True,
                        "confidence": 0.99,
                    },
                    "temporal_expression": temporal,
                    "evidence": [
                        EvidenceSpan(
                            source="content",
                            start_char=0,
                            end_char=len(doc.content),
                            text=doc.content,
                        ),
                        EvidenceSpan(
                            source="subject",
                            start_char=0,
                            end_char=len(doc.subject or ""),
                            text=doc.subject or "",
                        ),
                    ],
                }
            )
        ]
    )
    return OperationalExtractionResult(
        extraction=OperationalExtraction(observations=facts),
        extractor_version="operational-extraction.v1",
        model_provider="fixture",
        model_name="fixture-model",
        source_hash=source_document_hash(doc),
    )


@pytest.mark.parametrize(
    ("expression", "method", "expected"),
    [
        ("2026-10-03T14:00:00-04:00", "explicit_offset", "2026-10-03T18:00:00+00:00"),
        ("tomorrow at 3pm", "relative_day", "2026-09-23T19:00:00+00:00"),
        ("today at 09:30", "relative_day", "2026-09-22T13:30:00+00:00"),
        ("Friday at 2pm", "upcoming_weekday", "2026-09-25T18:00:00+00:00"),
    ],
)
def test_explicit_temporal_resolution_uses_source_time(expression, method, expected):
    result = resolve_temporal(expression, occurred_at=NOW, timezone_name="America/New_York")
    assert result.method == method
    assert result.resolved_at.isoformat() == expected


def test_relative_date_preserves_local_day_window():
    result = resolve_temporal("tomorrow", occurred_at=NOW, timezone_name="America/New_York")
    assert result.resolved_at is None
    assert result.start_at == datetime(2026, 9, 23, 4, tzinfo=UTC)
    assert result.end_at == datetime(2026, 9, 24, 4, tzinfo=UTC)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("tomorrow at 5pm UTC", "2026-09-23T17:00:00+00:00"),
        ("  tomorrow at 5pm utc  ", "2026-09-23T17:00:00+00:00"),
        ("tomorrow at 5pm America/Los_Angeles", "2026-09-24T00:00:00+00:00"),
    ],
)
def test_explicit_source_timezone_overrides_workspace_timezone(expression, expected):
    result = resolve_temporal(expression, occurred_at=NOW, timezone_name="America/New_York")
    assert result.resolved_at.isoformat() == expected


def test_relative_day_uses_the_explicit_source_timezone_at_midnight():
    result = resolve_temporal(
        "tomorrow at 5pm UTC",
        occurred_at=datetime(2026, 9, 23, 1, tzinfo=UTC),
        timezone_name="America/New_York",
    )
    assert result.resolved_at == datetime(2026, 9, 24, 17, tzinfo=UTC)


@pytest.mark.parametrize(
    "expression",
    [
        "tomorrow at 5pm PST",
        "tomorrow at 5pm CST",
        "tomorrow at 5pm America/Not_a_zone",
        "tomorrow at 5pm or Friday at 2pm",
    ],
)
def test_unrecognized_qualifiers_cannot_be_silently_ignored(expression):
    result = resolve_temporal(expression, occurred_at=NOW, timezone_name="America/New_York")
    assert result.resolved_at is None
    assert result.confidence < 0.9


def test_bare_clock_retains_am_pm_uncertainty():
    result = resolve_temporal("tomorrow at 5", occurred_at=NOW, timezone_name="America/New_York")
    assert result.resolved_at is None
    assert result.method == "ambiguous_clock"
    assert result.start_at == datetime(2026, 9, 23, 9, tzinfo=UTC)
    assert result.end_at == datetime(2026, 9, 23, 21, tzinfo=UTC)


@pytest.mark.parametrize(
    "expression",
    [
        "tomorrow PST",
        "tomorrow if approved",
        "2026-09-24 CST",
        "if approved tomorrow at 5pm",
    ],
)
def test_date_grammar_does_not_ignore_unsupported_qualifications(expression):
    result = resolve_temporal(expression, occurred_at=NOW, timezone_name="America/New_York")
    assert result.method == "unresolved"
    assert result.start_at is None and result.end_at is None and result.resolved_at is None
    assert result.confidence < 0.9


@pytest.mark.parametrize(
    "expression", ["by tomorrow", "before tomorrow's meeting", "tomorrow's review"]
)
def test_supported_date_only_expressions_keep_local_day_windows(expression):
    result = resolve_temporal(expression, occurred_at=NOW, timezone_name="America/New_York")
    assert result.method == "relative_day_window"
    assert result.start_at == datetime(2026, 9, 23, 4, tzinfo=UTC)
    assert result.end_at == datetime(2026, 9, 24, 4, tzinfo=UTC)
    assert result.resolved_at is None


@pytest.mark.parametrize("expression", ["soon", "03/04", "2026-02-30", "next Friday"])
def test_unknown_or_ambiguous_dates_are_not_invented(expression):
    result = resolve_temporal(expression, occurred_at=NOW, timezone_name="UTC")
    assert result.resolved_at is None
    assert result.method == "unresolved"


def test_dst_gap_and_fold_do_not_invent_instants():
    gap = resolve_temporal("2026-03-08 at 2:30", occurred_at=NOW, timezone_name="America/New_York")
    fold = resolve_temporal("2026-11-01 at 1:30", occurred_at=NOW, timezone_name="America/New_York")
    assert gap.method == "nonexistent_local_time"
    assert gap.resolved_at is None
    assert fold.method == "ambiguous_local_time"
    assert fold.resolved_at is None
    assert fold.end_at - fold.start_at == timedelta(hours=1)


@pytest.mark.asyncio
async def test_replay_has_one_commitment_and_bounded_evidence(resolution_db):
    db, connection = resolution_db
    doc = document(connection, "Please send budget tomorrow at 3pm.")
    result = extraction(doc, temporal="tomorrow at 3pm")
    first = await resolve_extraction(
        db, connection=connection, document=doc, result=result, timezone_name="America/New_York"
    )
    second = await resolve_extraction(
        db, connection=connection, document=doc, result=result, timezone_name="America/New_York"
    )
    assert first == second
    assert await db.scalar(select(func.count()).select_from(Commitment)) == 1
    assert await db.scalar(select(func.count()).select_from(OperationalObservation)) == 1
    assert await db.scalar(select(func.count()).select_from(CommitmentSource)) == 1
    evidence = await db.scalar(select(ObservationEvidence))
    assert "Please send" not in str(evidence.evidence_locator)
    commitment = await db.get(Commitment, first[0])
    assert commitment.status == "confirmed"
    assert commitment.due_at.replace(tzinfo=UTC) == datetime(2026, 9, 23, 19, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_type,subject_only,expected",
    [
        ("gmail_message", True, "candidate"),
        ("gmail_message", False, "confirmed"),
        ("calendar_event", True, "confirmed"),
    ],
)
async def test_headline_only_gmail_requires_review_despite_high_model_confidence(
    resolution_db, source_type, subject_only, expected
):
    db, connection = resolution_db
    doc = document(connection, "Please send the budget.", source_type=source_type)
    doc = doc.model_copy(update={"subject": "Please send the budget."})
    result = extraction(doc, confidence=0.98)
    candidate = result.extraction.observations[0]
    if subject_only:
        candidate = candidate.model_copy(update={"evidence": [candidate.evidence[1]]})
        result = replace(result, extraction=OperationalExtraction(observations=[candidate]))
    ids = await resolve_extraction(db, connection=connection, document=doc, result=result)
    commitment = await db.get(Commitment, ids[0])
    assert commitment.status == expected
    if expected == "candidate":
        assert commitment.confidence == 0.89
        assert commitment.intelligence_metadata["evidence_review_reason"] == "subject_only"
    else:
        assert commitment.confidence == 0.98


@pytest.mark.asyncio
async def test_subject_only_rsvp_stays_reviewable_and_cannot_complete_a_task(resolution_db):
    db, connection = resolution_db
    doc = document(connection, "You're invited to an optional webinar.")
    doc = doc.model_copy(update={"subject": "Webinar reminder: RSVP now for the systems seminar"})
    result = extraction(doc, action="RSVP", obj="systems seminar", kind="task", confidence=0.98)
    candidate = result.extraction.observations[0].model_copy(
        update={"evidence": [result.extraction.observations[0].evidence[1]]}
    )
    result = replace(result, extraction=OperationalExtraction(observations=[candidate]))
    ids = await resolve_extraction(db, connection=connection, document=doc, result=result)
    commitment = await db.get(Commitment, ids[0])
    assert commitment.status == "candidate"
    # Even a later high-confidence subject-only completion cannot change its state.
    followup = doc.model_copy(
        update={"external_id": "mail-2", "subject": "RSVP systems seminar completed"}
    )
    completion = extraction(
        followup, action="RSVP", obj="systems seminar", kind="completion", confidence=0.99
    )
    candidate = completion.extraction.observations[0].model_copy(
        update={"evidence": [completion.extraction.observations[0].evidence[1]]}
    )
    completion = replace(completion, extraction=OperationalExtraction(observations=[candidate]))
    assert (
        await resolve_extraction(db, connection=connection, document=followup, result=completion)
        == []
    )
    assert commitment.status == "candidate"


@pytest.mark.asyncio
async def test_identity_email_case_insensitive_and_display_name_not_identity(resolution_db):
    db, connection = resolution_db
    first = await resolve_identity(
        db,
        workspace_id=connection.workspace_id,
        identity=SourceIdentity(
            identity_type="email", identity_value="Owner@Example.com", display_name="Alex"
        ),
    )
    same = await resolve_identity(
        db,
        workspace_id=connection.workspace_id,
        identity=SourceIdentity(
            identity_type="email", identity_value="owner@example.com", display_name="A"
        ),
    )
    different = await resolve_identity(
        db,
        workspace_id=connection.workspace_id,
        identity=SourceIdentity(
            identity_type="email", identity_value="other@example.com", display_name="Alex"
        ),
    )
    assert first == same and first != different
    assert await db.scalar(select(func.count()).select_from(Person)) == 2


@pytest.mark.asyncio
async def test_low_confidence_and_marketing_do_not_create_commitments(resolution_db):
    db, connection = resolution_db
    low = document(connection, "Perhaps send budget.")
    assert (
        await resolve_extraction(
            db, connection=connection, document=low, result=extraction(low, confidence=0.5)
        )
        == []
    )
    marketing = document(
        connection,
        "Send budget today!",
        external_id="promo",
        metadata={"label_ids": ["CATEGORY_PROMOTIONS"]},
    )
    assert (
        await resolve_extraction(
            db, connection=connection, document=marketing, result=extraction(marketing)
        )
        == []
    )
    assert await db.scalar(select(func.count()).select_from(Commitment)) == 0
    assert await db.scalar(select(func.count()).select_from(OperationalObservation)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "", "<https://example.test/unsubscribe?private=token>"])
async def test_unlabelled_bulk_mail_header_reaches_marketing_suppression(resolution_db, header):
    import base64

    db, connection = resolution_db
    headers = [{"name": "Subject", "value": "Budget"}]
    if header is not None:
        headers.append({"name": "lIsT-UnSuBsCrIbE", "value": header})
    doc = gmail_document(
        {
            "id": "message-bulk-test",
            "labelIds": ["INBOX"],
            "payload": {
                "mimeType": "text/plain",
                "headers": headers,
                "body": {"data": base64.urlsafe_b64encode(b"Please send budget.").decode()},
            },
        },
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        now=NOW,
    )
    assert "unsubscribe?private" not in str(doc.metadata)
    ids = await resolve_extraction(db, connection=connection, document=doc, result=extraction(doc))
    assert len(ids) == (0 if header else 1)
    observation = await db.scalar(select(OperationalObservation))
    assert observation is None if header else observation.status == "ACTIVE"


@pytest.mark.asyncio
async def test_cross_workspace_and_hash_mismatch_fail_before_writes(resolution_db):
    db, connection = resolution_db
    doc = document(connection, "Please send budget.")
    other_doc = doc.model_copy(update={"workspace_id": uuid4()})
    with pytest.raises(PermissionError):
        await resolve_extraction(
            db, connection=connection, document=other_doc, result=extraction(other_doc)
        )
    with pytest.raises(ValueError, match="hash"):
        await resolve_extraction(
            db,
            connection=connection,
            document=doc,
            result=replace(extraction(doc), source_hash="0" * 64),
        )
    assert await db.scalar(select(func.count()).select_from(PersonIdentity)) == 0


@pytest.mark.asyncio
async def test_outgoing_budget_evidence_completes_original_request(resolution_db):
    db, connection = resolution_db
    incoming = document(connection, "Please send budget tomorrow at 3pm.")
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=incoming,
        result=extraction(incoming, temporal="tomorrow at 3pm"),
    )
    sent = document(
        connection,
        "Attached budget.",
        external_id="sent-1",
        author="owner@example.com",
        outgoing=True,
        occurred_at=NOW + timedelta(hours=1),
    )
    assert (
        await resolve_extraction(
            db, connection=connection, document=sent, result=extraction(sent, kind="completion")
        )
        == ids
    )
    commitment = await db.get(Commitment, ids[0])
    assert commitment.status == "completed"
    assert commitment.intelligence_metadata["completion_condition"] == "SEMANTIC_OUTCOME"
    assert await db.scalar(select(func.count()).select_from(Commitment)) == 1


@pytest.mark.asyncio
async def test_approval_request_waits_until_expected_counterparty_approves(resolution_db):
    db, connection = resolution_db
    request = document(connection, "Obtain budget approval.")
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=request,
        result=extraction(request, action="obtain", obj="budget approval"),
    )
    sent = document(
        connection,
        "Please provide budget approval.",
        external_id="sent",
        author="owner@example.com",
        outgoing=True,
        occurred_at=NOW + timedelta(hours=1),
    )
    await resolve_extraction(
        db,
        connection=connection,
        document=sent,
        result=extraction(sent, action="obtain", obj="budget approval", kind="waiting"),
    )
    commitment = await db.get(Commitment, ids[0])
    assert commitment.status == "waiting"
    unsolicited = document(
        connection,
        "Budget approval approved.",
        external_id="stranger",
        author="stranger@example.com",
        occurred_at=NOW + timedelta(hours=2),
    )
    await resolve_extraction(
        db,
        connection=connection,
        document=unsolicited,
        result=extraction(unsolicited, action="obtain", obj="budget approval", kind="completion"),
    )
    assert commitment.status == "waiting"
    reply = document(
        connection,
        "Budget approval approved.",
        external_id="reply",
        occurred_at=NOW + timedelta(hours=3),
    )
    await resolve_extraction(
        db,
        connection=connection,
        document=reply,
        result=extraction(reply, action="obtain", obj="budget approval", kind="completion"),
    )
    assert commitment.status == "completed"
    assert await db.scalar(select(func.count()).select_from(Commitment)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Budget not completed.",
        "Budget will be submitted.",
        "Budget completed?",
        "> Budget submitted.",
    ],
)
async def test_negated_future_quoted_or_question_completion_cannot_resolve(resolution_db, text):
    db, connection = resolution_db
    initial = document(connection, "Please send budget.")
    ids = await resolve_extraction(
        db, connection=connection, document=initial, result=extraction(initial)
    )
    sent = document(connection, text, external_id="sent", author="owner@example.com", outgoing=True)
    await resolve_extraction(
        db, connection=connection, document=sent, result=extraction(sent, kind="completion")
    )
    assert (await db.get(Commitment, ids[0])).status == "confirmed"


@pytest.mark.asyncio
async def test_calendar_update_supersedes_observation_and_old_delivery_cannot_undo(resolution_db):
    db, connection = resolution_db
    initial = document(
        connection, "Budget review 2026-09-23T15:00:00Z", source_type="calendar_event"
    )
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=initial,
        result=extraction(
            initial,
            action="attend",
            obj="Budget review",
            kind="meeting",
            temporal="2026-09-23T15:00:00Z",
        ),
    )
    changed = document(
        connection,
        "Budget review moved to 2026-09-23T17:00:00Z",
        source_type="calendar_event",
        occurred_at=NOW + timedelta(hours=1),
    )
    await resolve_extraction(
        db,
        connection=connection,
        document=changed,
        result=extraction(
            changed,
            action="attend",
            obj="Budget review",
            kind="meeting",
            temporal="2026-09-23T17:00:00Z",
        ),
    )
    commitment = await db.get(Commitment, ids[0])
    assert commitment.due_at.replace(tzinfo=UTC) == datetime(2026, 9, 23, 17, tzinfo=UTC)
    assert (
        await db.scalar(
            select(func.count())
            .select_from(OperationalObservation)
            .where(OperationalObservation.status == "SUPERSEDED")
        )
        == 1
    )
    assert (
        await resolve_extraction(
            db,
            connection=connection,
            document=initial,
            result=extraction(
                initial,
                action="attend",
                obj="Budget review",
                kind="meeting",
                temporal="2026-09-23T15:00:00Z",
            ),
        )
        == []
    )
    assert commitment.due_at.replace(tzinfo=UTC) == datetime(2026, 9, 23, 17, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cancellation_withdraws_inferred_calendar_state(resolution_db):
    db, connection = resolution_db
    initial = document(connection, "Budget review.", source_type="calendar_event")
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=initial,
        result=extraction(initial, action="attend", obj="Budget review", kind="meeting"),
    )
    cancelled = document(
        connection,
        "Cancelled calendar event",
        source_type="calendar_event",
        metadata={"status": "cancelled"},
        occurred_at=NOW + timedelta(hours=1),
    )
    await resolve_extraction(
        db, connection=connection, document=cancelled, result=extraction(cancelled, empty=True)
    )
    assert (await db.get(Commitment, ids[0])).status == "superseded"


@pytest.mark.asyncio
async def test_fresh_confirmed_calendar_revision_restores_only_source_withdrawal(resolution_db):
    db, connection = resolution_db
    initial = document(
        connection,
        "Budget review.",
        source_type="calendar_event",
        metadata={"status": "confirmed", "start_at": "2026-09-23T15:00:00Z"},
    )
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=initial,
        result=extraction(initial, action="attend", obj="Budget review", kind="meeting"),
    )
    cancelled = document(
        connection,
        "Cancelled calendar event",
        source_type="calendar_event",
        metadata={"status": "cancelled"},
        occurred_at=NOW + timedelta(hours=1),
    )
    await resolve_extraction(
        db, connection=connection, document=cancelled, result=extraction(cancelled, empty=True)
    )
    commitment = await db.get(Commitment, ids[0])
    assert commitment.status == "superseded"
    assert commitment.valid_until is not None
    restored = document(
        connection,
        "Budget review restored.",
        source_type="calendar_event",
        metadata={
            "status": "confirmed",
            "start_at": "2026-09-24T17:00:00Z",
            "end_at": "2026-09-24T18:00:00Z",
        },
        occurred_at=NOW + timedelta(hours=2),
    )
    assert (
        await resolve_extraction(
            db,
            connection=connection,
            document=restored,
            result=extraction(restored, action="attend", obj="Budget review", kind="meeting"),
        )
        == ids
    )
    assert commitment.status == "confirmed"
    assert commitment.valid_until is None
    assert commitment.due_at.replace(tzinfo=UTC) == datetime(2026, 9, 24, 17, tzinfo=UTC)
    assert commitment.intelligence_metadata["resolution"] == "UPDATE_EXISTING"
    assert "reason" not in commitment.intelligence_metadata
    # An old cancellation or a replay cannot undo or duplicate the restored state.
    await resolve_extraction(
        db, connection=connection, document=cancelled, result=extraction(cancelled, empty=True)
    )
    await resolve_extraction(
        db,
        connection=connection,
        document=restored,
        result=extraction(restored, action="attend", obj="Budget review", kind="meeting"),
    )
    assert commitment.status == "confirmed"
    assert await db.scalar(select(func.count()).select_from(Commitment)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "rejected"])
async def test_calendar_restoration_preserves_user_terminal_decisions(
    resolution_db, terminal_status
):
    db, connection = resolution_db
    initial = document(connection, "Budget review.", source_type="calendar_event")
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=initial,
        result=extraction(initial, action="attend", obj="Budget review", kind="meeting"),
    )
    commitment = await db.get(Commitment, ids[0])
    commitment.status = terminal_status
    restored = document(
        connection,
        "Budget review restored.",
        source_type="calendar_event",
        metadata={"status": "confirmed", "start_at": "2026-09-24T17:00:00Z"},
        occurred_at=NOW + timedelta(hours=2),
    )
    await resolve_extraction(
        db,
        connection=connection,
        document=restored,
        result=extraction(restored, action="attend", obj="Budget review", kind="meeting"),
    )
    assert commitment.status == terminal_status
    assert commitment.due_at is None


@pytest.mark.asyncio
async def test_user_completed_state_survives_historical_correction(resolution_db):
    db, connection = resolution_db
    initial = document(connection, "Please send budget.")
    ids = await resolve_extraction(
        db, connection=connection, document=initial, result=extraction(initial)
    )
    commitment = await db.get(Commitment, ids[0])
    commitment.status = "completed"
    changed = document(connection, "No active obligations.", occurred_at=NOW + timedelta(hours=1))
    await resolve_extraction(
        db, connection=connection, document=changed, result=extraction(changed, empty=True)
    )
    assert commitment.status == "completed"


@pytest.mark.asyncio
async def test_calendar_recurrence_instances_are_not_merged(resolution_db):
    db, connection = resolution_db
    for number in (1, 2):
        doc = document(
            connection,
            "Budget review.",
            external_id=f"instance-{number}",
            source_type="calendar_event",
        )
        await resolve_extraction(
            db,
            connection=connection,
            document=doc,
            result=extraction(doc, action="attend", obj="Budget review", kind="meeting"),
        )
    assert await db.scalar(select(func.count()).select_from(Commitment)) == 2


@pytest.mark.asyncio
async def test_meeting_context_requires_relevance_and_adds_graph_relation(resolution_db):
    db, connection = resolution_db
    meeting = document(
        connection,
        "Sales meeting.",
        external_id="event",
        source_type="calendar_event",
        metadata={"start_at": "2026-09-23T15:00:00Z", "end_at": "2026-09-23T16:00:00Z"},
    )
    meeting_ids = await resolve_extraction(
        db,
        connection=connection,
        document=meeting,
        result=extraction(meeting, action="attend", obj="Sales meeting", kind="meeting"),
    )
    request = document(connection, "Please send budget before tomorrow's meeting.")
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=request,
        result=extraction(request, temporal="before tomorrow's meeting"),
    )
    assert (await db.get(Commitment, ids[0])).due_at is None
    meeting_commitment = await db.get(Commitment, meeting_ids[0])
    meeting_commitment.title = "Budget review"
    request = document(
        connection,
        "Please send budget before tomorrow's meeting.",
        external_id="second",
        occurred_at=NOW + timedelta(minutes=10),
    )
    await resolve_extraction(
        db,
        connection=connection,
        document=request,
        result=extraction(request, temporal="before tomorrow's meeting"),
    )
    commitment = await db.get(Commitment, ids[0])
    assert commitment.due_at.replace(tzinfo=UTC) == datetime(2026, 9, 23, 15, tzinfo=UTC)
    relation = await db.scalar(select(CommitmentRelation))
    assert relation.from_commitment_id == commitment.id
    assert relation.to_commitment_id == meeting_commitment.id


@pytest.mark.asyncio
async def test_older_thread_message_cannot_replace_newer_deadline(resolution_db):
    db, connection = resolution_db
    initial = document(connection, "Please send budget 2026-09-24T15:00:00Z.")
    ids = await resolve_extraction(
        db,
        connection=connection,
        document=initial,
        result=extraction(initial, temporal="2026-09-24T15:00:00Z"),
    )
    old = document(
        connection,
        "Send budget instead 2026-09-23T15:00:00Z.",
        external_id="older",
        occurred_at=NOW - timedelta(days=1),
    )
    await resolve_extraction(
        db,
        connection=connection,
        document=old,
        result=extraction(old, temporal="2026-09-23T15:00:00Z"),
    )
    commitment = await db.get(Commitment, ids[0])
    assert commitment.due_at.replace(tzinfo=UTC) == datetime(2026, 9, 24, 15, tzinfo=UTC)
    assert (
        await db.scalar(
            select(func.count())
            .select_from(OperationalObservation)
            .where(OperationalObservation.status == "HISTORICAL")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_ambiguous_state_match_does_not_create_duplicate_or_complete_wrong_item(
    resolution_db,
):
    db, connection = resolution_db
    for number, title in enumerate(("budget report original", "budget report revision")):
        doc = document(connection, f"Please send {title}.", external_id=f"message-{number}")
        await resolve_extraction(
            db, connection=connection, document=doc, result=extraction(doc, obj=title)
        )
    doc = document(
        connection,
        "Attached budget report.",
        external_id="sent",
        outgoing=True,
        author="owner@example.com",
    )
    assert (
        await resolve_extraction(
            db,
            connection=connection,
            document=doc,
            result=extraction(doc, kind="completion", obj="budget report"),
        )
        == []
    )
    commitments = list(await db.scalars(select(Commitment)))
    assert len(commitments) == 2
    assert all(c.status == "confirmed" for c in commitments)


@pytest.mark.asyncio
async def test_unresolved_subject_caps_completion_confidence(resolution_db):
    db, connection = resolution_db
    initial = document(connection, "Please send budget.")
    ids = await resolve_extraction(
        db, connection=connection, document=initial, result=extraction(initial)
    )
    doc = document(
        connection,
        "Attached budget.",
        external_id="sent",
        outgoing=True,
        author="owner@example.com",
    )
    result = extraction(doc, kind="completion")
    candidate = result.extraction.observations[0].model_copy(
        update={"subject_text": "Unknown Alex"}
    )
    result = replace(result, extraction=OperationalExtraction(observations=[candidate]))
    await resolve_extraction(db, connection=connection, document=doc, result=result)
    assert (await db.get(Commitment, ids[0])).status == "confirmed"


@pytest.mark.asyncio
async def test_time_state_meeting_ends_but_passed_task_is_not_completed(resolution_db):
    db, connection = resolution_db
    meeting = document(
        connection,
        "Budget review.",
        external_id="event",
        source_type="calendar_event",
        metadata={"start_at": "2026-09-23T15:00:00Z", "end_at": "2026-09-23T16:00:00Z"},
    )
    meeting_ids = await resolve_extraction(
        db,
        connection=connection,
        document=meeting,
        result=extraction(meeting, action="attend", obj="Budget review", kind="meeting"),
    )
    task = document(connection, "Please send budget 2026-09-23T15:00:00Z.")
    task_ids = await resolve_extraction(
        db,
        connection=connection,
        document=task,
        result=extraction(task, temporal="2026-09-23T15:00:00Z"),
    )
    after = datetime(2026, 9, 24, tzinfo=UTC)
    meeting_commitment = await db.get(Commitment, meeting_ids[0])
    task_commitment = await db.get(Commitment, task_ids[0])
    assert await reevaluate_commitment(db, commitment=meeting_commitment, now=after)
    assert await reevaluate_commitment(db, commitment=task_commitment, now=after)
    assert meeting_commitment.status == "completed"
    assert task_commitment.status == "attention"
    assert not await reevaluate_commitment(db, commitment=task_commitment, now=after)


@pytest.mark.asyncio
async def test_grounded_people_dates_and_relationships_are_preserved(resolution_db):
    db, connection = resolution_db
    doc = document(
        connection,
        "Please send budget and invoice. Alice <alice@example.com> says "
        "budget depends on invoice tomorrow.",
    )
    result = extraction(doc)
    payload = result.extraction.model_dump()
    evidence = payload["observations"][0]["evidence"]
    payload["observations"].append(
        {
            **payload["observations"][0],
            "object_text": "invoice",
        }
    )
    payload["people"] = [
        {
            "name": "Alice",
            "identity_type": "email",
            "identity_value": "alice@example.com",
            "confidence": 0.97,
            "evidence": evidence,
        }
    ]
    payload["temporals"] = [
        {"expression": "tomorrow", "kind": "deadline", "confidence": 0.95, "evidence": evidence}
    ]
    payload["relationships"] = [
        {
            "relationship_type": "depends_on",
            "subject_text": "budget",
            "object_text": "invoice",
            "confidence": 0.97,
            "evidence": evidence,
        }
    ]
    result = replace(result, extraction=OperationalExtraction.model_validate(payload))
    await resolve_extraction(db, connection=connection, document=doc, result=result)
    assert await db.scalar(select(func.count()).select_from(Commitment)) == 2
    assert await db.scalar(select(func.count()).select_from(OperationalObservation)) == 5
    assert await db.scalar(select(func.count()).select_from(CommitmentRelation)) == 1
    assert await db.scalar(select(func.count()).select_from(PersonIdentity)) == 3
    await resolve_extraction(db, connection=connection, document=doc, result=result)
    assert await db.scalar(select(func.count()).select_from(OperationalObservation)) == 5
    assert await db.scalar(select(func.count()).select_from(CommitmentRelation)) == 1
