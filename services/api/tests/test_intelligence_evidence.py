import asyncio
import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_intelligence_runtime import runtime_env  # noqa: F401

from navox.api import intelligence_evidence as api
from navox.db.models import (
    AuditEvent,
    Commitment,
    CommitmentSource,
    Connection,
    ObservationEvidence,
    User,
    WorkspaceMembership,
)
from navox.intelligence.extraction import (
    EvidenceSpan,
    OperationalExtraction,
    OperationalExtractionResult,
    OperationalObservationCandidate,
    source_document_hash,
)
from navox.intelligence.resolution import resolve_extraction
from navox.providers.google_oauth import GoogleAccessTokenError
from navox.providers.google_sources import GMAIL_READ_SCOPE, GoogleSourceError, gmail_document

QUOTE = "Please send the budget."
PRIVATE = "Private incidental message text."


@pytest_asyncio.fixture
async def owned_evidence(runtime_env, monkeypatch):  # noqa: F811
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
            external_account_id="evidence-test",
            granted_scopes=[GMAIL_READ_SCOPE],
        )
        db.add(connection)
        await db.flush()
        document = gmail_document(
            {
                "id": "mail1",
                "threadId": "thread1",
                "internalDate": "1790078400000",
                "labelIds": ["INBOX"],
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": "Budget"},
                        {"name": "From", "value": "Pat <pat@example.com>"},
                        {"name": "To", "value": "owner@example.com"},
                    ],
                    "body": {
                        "data": base64.urlsafe_b64encode(f"{QUOTE} {PRIVATE}".encode()).decode()
                    },
                },
            },
            workspace_id=member.workspace_id,
            connection_id=connection.id,
            now=datetime.now(UTC),
        )
        result = OperationalExtractionResult(
            extraction=OperationalExtraction(
                observations=[
                    OperationalObservationCandidate(
                        observation_type="request",
                        action_text="send",
                        object_text="the budget",
                        confidence=0.86,
                        evidence=[
                            EvidenceSpan(
                                source="content", start_char=0, end_char=len(QUOTE), text=QUOTE
                            )
                        ],
                    )
                ]
            ),
            extractor_version="operational-extraction.v1",
            model_provider="fixture",
            model_name="fixture",
            source_hash=source_document_hash(document),
        )
        await resolve_extraction(db, connection=connection, document=document, result=result)
        evidence = await db.scalar(select(ObservationEvidence))
        await db.commit()
        state = SimpleNamespace(
            client=client,
            factory=factory,
            document=document,
            evidence_id=evidence.id,
            connection_id=connection.id,
            user_id=user.id,
            url=f"/api/v1/intelligence/evidence/{evidence.id}",
            extraction=result,
        )
    state.token = AsyncMock(return_value="private-token")
    state.read = AsyncMock(return_value=document)
    monkeypatch.setattr(api, "access_token_for_connection", state.token)
    monkeypatch.setattr(api.GoogleSourceGateway, "gmail_message", state.read)
    return state


@pytest.mark.asyncio
async def test_today_links_to_bounded_evidence_without_automatic_reads_or_body_storage(
    owned_evidence,
):
    state = owned_evidence
    today = await state.client.get("/api/v1/today")
    source = today.json()["needs_attention"][0]["sources"][0]
    assert source["evidence_id"] == str(state.evidence_id)
    assert QUOTE not in today.text and PRIVATE not in today.text
    state.read.assert_not_awaited()
    state.token.assert_not_awaited()
    async with state.factory() as db:
        audits_before = await db.scalar(select(func.count()).select_from(AuditEvent))
    response = await state.client.get(state.url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "evidence_id": str(state.evidence_id),
        "excerpts": [{"source": "content", "text": QUOTE}],
    }
    assert PRIVATE not in response.text and "private-token" not in response.text
    state.read.assert_awaited_once()
    assert state.read.await_args.kwargs["external_id"] == "mail1"
    async with state.factory() as db:
        evidence = await db.get(ObservationEvidence, state.evidence_id)
        assert QUOTE not in str(evidence.evidence_locator)
        assert PRIVATE not in str(evidence.evidence_locator)
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == audits_before
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("distinct_passage", [False, True])
async def test_today_deduplicates_identical_citations_but_keeps_distinct_evidence(
    owned_evidence, distinct_passage
):
    state = owned_evidence
    candidate = state.extraction.extraction.observations[0].model_copy(update={"confidence": 0.88})
    if distinct_passage:
        candidate = candidate.model_copy(
            update={
                "object_text": "Budget",
                "evidence": [
                    EvidenceSpan(source="subject", start_char=0, end_char=6, text="Budget")
                ],
            }
        )
    result = replace(state.extraction, extraction=OperationalExtraction(observations=[candidate]))
    async with state.factory() as db:
        connection = await db.get(Connection, state.connection_id)
        await resolve_extraction(db, connection=connection, document=state.document, result=result)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(CommitmentSource)) == 2
        assert await db.scalar(select(func.count()).select_from(ObservationEvidence)) == 2
    today = (await state.client.get("/api/v1/today")).json()
    sources = today["needs_attention"][0]["sources"]
    assert len(sources) == (2 if distinct_passage else 1)
    assert all(source["connection_id"] == str(state.connection_id) for source in sources)
    state.read.assert_not_awaited()
    passages = []
    for source in sources:
        response = await state.client.get(f"/api/v1/intelligence/evidence/{source['evidence_id']}")
        assert response.status_code == 200
        passages.extend(excerpt["text"] for excerpt in response.json()["excerpts"])
    assert QUOTE in passages
    assert ("Budget" in passages) is distinct_passage


@pytest.mark.asyncio
async def test_owner_can_dismiss_saved_ai_task_and_replays_cannot_resurrect_it(owned_evidence):
    state = owned_evidence
    async with state.factory() as db:
        commitment = await db.scalar(select(Commitment))
        commitment.status = "confirmed"
        commitment.confidence = 0.98
        commitment_id = commitment.id
        await db.commit()
    url = f"/api/v1/commitments/{commitment_id}/dismiss"
    first = await state.client.post(url)
    assert first.status_code == 200 and first.json()["status"] == "rejected"
    assert (await state.client.post(url)).json()["status"] == "rejected"
    async with state.factory() as db:
        connection = await db.get(Connection, state.connection_id)
        # A fresh revision, even with a high-confidence body-backed proposal,
        # must not undo the owner's rejection of the same obligation.
        revised = state.document.model_copy(
            update={"content": state.document.content + " Revised footer."}
        )
        candidate = state.extraction.extraction.observations[0].model_copy(
            update={"confidence": 0.99}
        )
        result = replace(
            state.extraction,
            source_hash=source_document_hash(revised),
            extraction=OperationalExtraction(observations=[candidate]),
        )
        await resolve_extraction(db, connection=connection, document=revised, result=result)
        await db.commit()
        assert (await db.get(Commitment, commitment_id)).status == "rejected"
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 1
        audits = list(
            await db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "commitment.dismissed")
            )
        )
        assert len(audits) == 1 and audits[0].event_metadata == {"reason": "not_a_task"}
    assert (await state.client.get("/api/v1/today")).json()["total"] == 0
    state.read.assert_not_awaited()
    state.token.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["other_owner", "manual", "completed", "superseded"])
async def test_dismiss_cannot_target_another_owner_manual_or_terminal_task(owned_evidence, mode):
    state = owned_evidence
    async with state.factory() as db:
        commitment = await db.scalar(select(Commitment))
        commitment_id = commitment.id
        if mode == "manual":
            commitment.created_by = "user"
        elif mode in {"completed", "superseded"}:
            commitment.status = mode
        await db.commit()
    if mode == "other_owner":
        await state.client.post(
            "/api/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "twelve-character-password",
                "display_name": "Other",
            },
        )
    response = await state.client.post(f"/api/v1/commitments/{commitment_id}/dismiss")
    assert response.status_code == (404 if mode == "other_owner" else 409)
    async with state.factory() as db:
        commitment = await db.get(Commitment, commitment_id)
        assert commitment.status != "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["other_account", "unknown_id", "paused", "revoked", "inactive"])
async def test_evidence_rejects_unauthorized_reads_before_token_or_google(owned_evidence, mode):
    state = owned_evidence
    expected = 403
    if mode == "other_account":
        await state.client.post(
            "/api/v1/auth/register",
            json={
                "email": "other@example.com",
                "password": "twelve-character-password",
                "display_name": "Other",
            },
        )
        expected = 404
    elif mode == "unknown_id":
        state.url = f"/api/v1/intelligence/evidence/{uuid4()}"
        expected = 404
    else:
        async with state.factory() as db:
            connection = await db.get(Connection, state.connection_id)
            if mode == "paused":
                user = await db.get(User, state.user_id)
                user.agent_paused = True
            elif mode == "revoked":
                connection.granted_scopes = []
            else:
                connection.status = "disconnected"
            await db.commit()
    response = await state.client.get(state.url)
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    assert QUOTE not in response.text
    state.token.assert_not_awaited()
    state.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_evidence_honors_narrowed_refresh_grants_before_mail_read(owned_evidence):
    state = owned_evidence

    async def narrow(database, *, connection, settings):
        connection.granted_scopes = []
        return "private-token"

    state.token.side_effect = narrow
    response = await state.client.get(state.url)
    assert response.status_code == 403
    state.read.assert_not_awaited()
    async with state.factory() as db:
        connection = await db.get(Connection, state.connection_id)
        assert connection.granted_scopes == []


@pytest.mark.asyncio
async def test_evidence_rechecks_pause_after_slow_read(owned_evidence):
    state = owned_evidence

    async def pause(*args, **kwargs):
        async with state.factory() as db:
            user = await db.get(User, state.user_id)
            user.agent_paused = True
            await db.commit()
        return state.document

    state.read.side_effect = pause
    response = await state.client.get(state.url)
    assert response.status_code == 403
    assert QUOTE not in response.text


@pytest.mark.asyncio
async def test_evidence_obeys_existing_gmail_cooldown(owned_evidence):
    state = owned_evidence
    async with state.factory() as db:
        connection = await db.get(Connection, state.connection_id)
        db.add(
            AuditEvent(
                user_id=connection.user_id,
                workspace_id=connection.workspace_id,
                event_type="intelligence.source.failed",
                actor_type="system",
                entity_type="connection",
                entity_id=connection.id,
                event_metadata={
                    "source": "gmail",
                    "error_diagnostic": {"code": "google_rate_limited"},
                    "retry_not_before": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
                },
            )
        )
        await db.commit()
    response = await state.client.get(state.url)
    assert response.status_code == 429
    state.token.assert_not_awaited()
    state.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_evidence_deadline_cancels_a_stalled_google_read(owned_evidence, monkeypatch):
    state = owned_evidence
    real_timeout = asyncio.timeout
    cancelled = asyncio.Event()

    async def stall(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(api.asyncio, "timeout", lambda seconds: real_timeout(0.05))
    state.read.side_effect = stall
    response = await state.client.get(state.url)
    assert response.status_code == 504
    assert cancelled.is_set()
    assert QUOTE not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["content", "deleted", "unsub_metadata"])
async def test_evidence_detects_changed_or_deleted_source_and_supports_old_normalization(
    owned_evidence, change
):
    state = owned_evidence
    if change == "content":
        update = {"content": "Changed private content"}
        expected = 409
    elif change == "deleted":
        update = {"metadata": dict(state.document.metadata, status="deleted")}
        expected = 410
    else:
        update = {"metadata": dict(state.document.metadata, list_unsubscribe=True)}
        expected = 200
    state.read.return_value = state.document.model_copy(update=update)
    response = await state.client.get(state.url)
    assert response.status_code == expected
    if expected != 200:
        assert QUOTE not in response.text and "Changed private content" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "locator",
    [
        None,
        {"spans": []},
        {"spans": [{"source": "content", "start_char": True, "end_char": 5}]},
        {"spans": [{"source": "content", "start_char": 0, "end_char": 9999}]},
        {"spans": [{"source": "body", "start_char": 0, "end_char": 5}]},
    ],
)
async def test_evidence_rejects_unverifiable_locators(owned_evidence, locator):
    state = owned_evidence
    async with state.factory() as db:
        evidence = await db.get(ObservationEvidence, state.evidence_id)
        evidence.evidence_locator = locator
        await db.commit()
    response = await state.client.get(state.url)
    assert response.status_code == 409
    assert QUOTE not in response.text and PRIVATE not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (GoogleSourceError("provider private failure"), 502),
        (GoogleAccessTokenError("private credential failure"), 403),
        (TimeoutError("private timeout"), 504),
    ],
)
async def test_evidence_failures_are_sanitized(owned_evidence, error, status):
    state = owned_evidence
    if isinstance(error, GoogleAccessTokenError):
        state.token.side_effect = error
    else:
        state.read.side_effect = error
    response = await state.client.get(state.url)
    assert response.status_code == status
    assert "private" not in response.text
    assert response.headers["cache-control"] == "no-store"
