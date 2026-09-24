"""Compose gate with real PostgreSQL/Temporal and synthetic Google/model I/O.

No production service imports this harness. Provider patches exist only in this
process; the task queue and database workspace are unique to each run.
"""

import asyncio
import base64
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.client import Client, WorkflowFailureError
from temporalio.worker import Worker

from integration.gmail_recheck import verify_gmail_recheck_concurrency
from navox.ai.gateway import AIGateway, StructuredOutputResponse
from navox.api.intelligence_sync import _describe_sync
from navox.core.settings import Settings
from navox.db.models import (
    Action,
    Approval,
    AuditEvent,
    Commitment,
    Connection,
    ConnectorConnection,
    ConnectorSyncRun,
    GmailSyncPlan,
    IncomingEvent,
    IntelligenceCursor,
    IntelligenceSourceReceipt,
    ObservationEvidence,
    OperationalObservation,
    ProactivePreference,
    ProactiveSignal,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence.activities import process_source_activity, refresh_intelligence_activity
from navox.intelligence.dispatcher import dispatch_feedback, dispatch_source
from navox.intelligence.feedback import record_feedback
from navox.intelligence.ingestion import process_connection as actual_process_connection
from navox.intelligence.jobs import SourceWork, WorkspaceWork
from navox.intelligence.source_cooldown import GoogleSourceBusyError, source_cooldown
from navox.proactive.engine import aware, evaluate_workspace
from navox.providers.google_sources import (
    CALENDAR_READ_SCOPE,
    GMAIL_READ_SCOPE,
    GoogleSourceGateway,
)
from navox.today.projection import build_today_projection
from navox.workflows.intelligence import (
    AttentionEvaluationWorkflow,
    FeedbackLearningWorkflow,
    ProcessSourceEventWorkflow,
    TodayRefreshWorkflow,
)


class IntegrationSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


class ProviderFixtures:
    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.reads = 0
        self.extractions = 0
        self.google_disabled = False
        self.google_quota_limited = False
        self.quota_started: asyncio.Event | None = None
        self.quota_release: asyncio.Event | None = None
        self.resume_enabled = False
        self.resume_quota_limited = False
        self.resume_reads: Counter[str] = Counter()
        self.resume_full_order: list[str] = []
        self.resume_started: asyncio.Event | None = None
        self.resume_release: asyncio.Event | None = None
        self.busy_started: asyncio.Event | None = None
        self.busy_connection_id: UUID | None = None
        self.busy_deferrals = 0

    async def google(self, request: httpx.Request) -> httpx.Response:
        self.reads += 1
        assert request.headers["authorization"] == "Bearer synthetic-access-token"
        if self.resume_enabled:
            return await self.gmail_resume(request)
        if self.google_quota_limited:
            if self.quota_started is not None and self.quota_release is not None:
                self.quota_started.set()
                await asyncio.wait_for(self.quota_release.wait(), timeout=30)
            return httpx.Response(
                403,
                json={
                    "error": {
                        "errors": [{"reason": "dailyLimitExceeded"}],
                        "message": "synthetic-private-quota-details",
                    }
                },
            )
        if self.google_disabled:
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": 403,
                        "message": "synthetic-private-provider-message",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                "reason": "SERVICE_DISABLED",
                                "metadata": {"consumer": "projects/synthetic-private-project"},
                            }
                        ],
                    }
                },
            )
        path = request.url.path
        if path.endswith("/history"):
            return httpx.Response(
                200,
                json={
                    "historyId": "101",
                    "history": [{"messagesAdded": [{"message": {"id": "budget-message"}}]}],
                },
            )
        if path.endswith("/messages/budget-message"):
            return httpx.Response(
                200,
                json={
                    "id": "budget-message",
                    "threadId": "budget-thread",
                    "internalDate": str(int(self.now.timestamp() * 1000)),
                    "labelIds": ["INBOX"],
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "From", "value": "Maya <maya@example.test>"},
                            {"name": "To", "value": "Owner <owner@example.test>"},
                            {"name": "Subject", "value": "Budget request"},
                        ],
                        "body": {
                            "data": base64.urlsafe_b64encode(b"Please send the budget.").decode()
                        },
                    },
                },
            )
        if path.endswith("/calendars/primary/events"):
            return httpx.Response(
                200,
                json={
                    "nextSyncToken": "calendar-101",
                    "items": [
                        {
                            "id": "research-meeting",
                            "summary": "Research review",
                            "description": "Attend the research review.",
                            "updated": self.now.isoformat(),
                            "status": "confirmed",
                            "start": {"dateTime": (self.now + timedelta(hours=1)).isoformat()},
                            "end": {"dateTime": (self.now + timedelta(hours=2)).isoformat()},
                            "organizer": {"email": "maya@example.test"},
                            "attendees": [{"email": "owner@example.test"}],
                        }
                    ],
                },
            )
        raise AssertionError("Unexpected fixture HTTP request")

    async def gmail_resume(self, request: httpx.Request) -> httpx.Response:
        """Fail after the first processed message, then resume across workers."""
        path = request.url.path
        if path.endswith("/profile"):
            self.resume_reads["profile"] += 1
            return httpx.Response(200, json={"historyId": "201"})
        if path.endswith("/messages"):
            self.resume_reads["list"] += 1
            return httpx.Response(
                200,
                json={"messages": [{"id": f"resume-{position}"} for position in (3, 2, 1)]},
            )
        if path.endswith("/history"):
            self.resume_reads["history"] += 1
            assert request.url.params["startHistoryId"] == "201"
            return httpx.Response(200, json={"historyId": "201", "history": []})
        message_id = path.rsplit("/", 1)[-1]
        assert message_id in {"resume-1", "resume-2", "resume-3"}
        position = int(message_id.rsplit("-", 1)[-1])
        occurred_at = self.now + timedelta(seconds=position)
        metadata = {
            "id": message_id,
            "internalDate": str(int(occurred_at.timestamp() * 1000)),
        }
        assert request.url.params["format"] == "full"
        if request.url.params["fields"] == "id,internalDate":
            self.resume_reads[f"metadata:{message_id}"] += 1
            return httpx.Response(200, json=metadata)
        assert "payload" in request.url.params["fields"]
        self.resume_reads[f"full:{message_id}"] += 1
        self.resume_full_order.append(message_id)
        if message_id == "resume-2":
            if self.resume_quota_limited:
                return httpx.Response(
                    403,
                    json={"error": {"errors": [{"reason": "dailyLimitExceeded"}]}},
                )
            if self.resume_started is not None and self.resume_release is not None:
                self.resume_started.set()
                await asyncio.wait_for(self.resume_release.wait(), timeout=30)
        return httpx.Response(
            200,
            json={
                **metadata,
                "threadId": f"resume-thread-{position}",
                "labelIds": ["INBOX"],
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": "Maya <maya@example.test>"},
                        {"name": "To", "value": "Owner <owner@example.test>"},
                        {"name": "Subject", "value": f"Resume request {position}"},
                    ],
                    "body": {"data": base64.urlsafe_b64encode(b"Please send the budget.").decode()},
                },
            },
        )

    async def generate_json(
        self, *, schema_name: str, schema: dict[str, Any], instructions: str, input_text: str
    ) -> StructuredOutputResponse:
        self.extractions += 1
        data = json.loads(input_text)
        content = str(data["content"])
        meeting = data["source_type"] == "calendar_event"
        return StructuredOutputResponse(
            data={
                "schema_version": "operational-extraction.v2",
                "observations": [
                    {
                        "observation_type": "meeting" if meeting else "request",
                        "action_text": "attend" if meeting else "send",
                        "object_text": "the research review" if meeting else "the budget",
                        "confidence": 0.97,
                        "email_relevance": None
                        if meeting
                        else {
                            "intent": "action_required",
                            "basis": "direct_request",
                            "applies_to_user": True,
                            "confidence": 0.99,
                        },
                        "evidence": [
                            {
                                "source": "content",
                                "start_char": 0,
                                "end_char": len(content),
                                "text": content,
                            }
                        ],
                    }
                ],
            },
            provider="synthetic-fixture",
            model="compose-smoke-v1",
        )


async def seed(
    sessions: async_sessionmaker[AsyncSession], user_id: UUID, workspace_id: UUID
) -> tuple[UUID, dict[str, UUID]]:
    connection_id = uuid4()
    events = {source: uuid4() for source in ("gmail", "calendar")}
    async with sessions() as database:
        database.add_all(
            [
                User(id=user_id, email=f"intelligence-ci-{user_id}@example.test", timezone="UTC"),
                Workspace(id=workspace_id, name="Isolated intelligence integration fixture"),
            ]
        )
        await database.flush()
        database.add(WorkspaceMembership(user_id=user_id, workspace_id=workspace_id))
        database.add(
            Connection(
                id=connection_id,
                user_id=user_id,
                workspace_id=workspace_id,
                provider="google",
                external_account_id=f"fixture-{connection_id}",
                external_email="owner@example.test",
                status="active",
                granted_scopes=[GMAIL_READ_SCOPE, CALENDAR_READ_SCOPE],
            )
        )
        await database.flush()
        for source, event_id in events.items():
            database.add(
                IntelligenceCursor(connection_id=connection_id, source=source, cursor="100")
            )
            database.add(
                IncomingEvent(
                    id=event_id,
                    connection_id=connection_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    provider="google",
                    source=source,
                    event_type="synthetic.change",
                    external_event_id=str(event_id),
                    payload_hash="0" * 64,
                )
            )
        await database.commit()
    return connection_id, events


async def source_fence_snapshot(
    sessions: async_sessionmaker[AsyncSession], connection_id: UUID
) -> tuple[UUID | None, int, UUID | None, str | None]:
    """Provider I/O must leave authority rows unlocked while its attempt stays fenced."""
    connector_id = uuid5(NAMESPACE_URL, f"navox:google-gmail:{connection_id}")
    async with sessions() as database:
        # NOWAIT deliberately fails if a provider request retains either lock.
        # The old harness required exactly that obsolete lock-over-I/O behavior.
        connection = await database.scalar(
            select(ConnectorConnection)
            .where(ConnectorConnection.id == connector_id)
            .with_for_update(nowait=True)
        )
        legacy = await database.scalar(
            select(Connection).where(Connection.id == connection_id).with_for_update(nowait=True)
        )
        assert legacy is not None and connection is not None
        assert connection.sync_lease_token is not None
        assert connection.sync_lease_expires_at is not None
        assert aware(connection.sync_lease_expires_at) > datetime.now(UTC)
        run = await database.get(ConnectorSyncRun, connection.sync_run_id)
        assert run is not None and run.status == "running"
        assert await source_cooldown(database, connection_id, "gmail") is None
        return (
            connection.sync_run_id,
            connection.sync_generation,
            connection.sync_lease_token,
            run.checkpoint_cursor,
        )


async def wait_for_source_deferral(
    sessions: async_sessionmaker[AsyncSession],
    connection_id: UUID,
    fixtures: ProviderFixtures,
    before: tuple[UUID | None, int, UUID | None, str | None],
) -> None:
    """Observe a real competing activity hitting the fence, not an arbitrary sleep."""
    assert fixtures.busy_started is not None
    await asyncio.wait_for(fixtures.busy_started.wait(), timeout=15)
    assert fixtures.busy_connection_id == connection_id
    assert await source_fence_snapshot(sessions, connection_id) == before


async def verify(
    sessions: async_sessionmaker[AsyncSession],
    client: Client,
    settings: Settings,
    fixtures: ProviderFixtures,
    user_id: UUID,
    workspace_id: UUID,
    connection_id: UUID,
    events: dict[str, UUID],
) -> None:
    async def process(source: str, event_id: UUID | None = None) -> int:
        workflow_id = await dispatch_source(
            SourceWork(
                str(connection_id),
                str(user_id),
                str(workspace_id),
                source,
                str(event_id) if event_id else None,
            ),
            settings=settings,
            request_id=str(uuid4()),
        )
        result = await asyncio.wait_for(client.get_workflow_handle(workflow_id).result(), 120)
        assert isinstance(result, int)
        status = await _describe_sync(workflow_id, settings.temporal_target)
        assert status.status == "completed"
        assert status.commitment_count == result
        assert status.error is None
        return result

    for source, event_id in events.items():
        assert await process(source, event_id) == 1
    assert fixtures.extractions == 2
    assert await process("gmail") == 1
    assert fixtures.extractions == 2
    async with sessions() as database:
        for event_id in events.values():
            event = await database.get(IncomingEvent, event_id)
            assert event is not None and event.intelligence_status == "completed"
        for model, column, value in (
            (Commitment, Commitment.workspace_id, workspace_id),
            (OperationalObservation, OperationalObservation.workspace_id, workspace_id),
            (IntelligenceSourceReceipt, IntelligenceSourceReceipt.connection_id, connection_id),
            (ObservationEvidence, ObservationEvidence.connection_id, connection_id),
        ):
            count = await database.scalar(
                select(func.count()).select_from(model).where(column == value)
            )
            assert count == 2
        projection = await build_today_projection(
            database, user_id=user_id, workspace_id=workspace_id, timezone_name="UTC"
        )
        items = [*projection.needs_attention, *projection.coming_up]
        assert len(items) == 2 and projection.total == 2
        assert all(item.sources and item.sources[0].evidence_locator for item in items)
        assert all(item.reasons and len(item.factors) == 10 for item in items)
        target = next(item for item in items if item.type != "meeting")
        request_id = uuid4()
        _, applied, weights = await record_feedback(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            target_id=target.id,
            request_id=request_id,
            feedback_type="more_like_this",
        )
        assert applied and weights["task"] == 0.01
        _, applied, _ = await record_feedback(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            target_id=target.id,
            request_id=request_id,
            feedback_type="more_like_this",
        )
        assert not applied
        await database.commit()
    feedback_workflow = await dispatch_feedback(
        WorkspaceWork(str(user_id), str(workspace_id), str(target.id)),
        settings=settings,
        request_id=str(request_id),
    )
    await asyncio.wait_for(client.get_workflow_handle(feedback_workflow).result(), 120)
    # Exercise failure serialization through the real worker and Temporal history.
    # Provider I/O remains synthetic and the production retry policy is unchanged.
    reads_before_failure, extractions_before_failure = fixtures.reads, fixtures.extractions
    fixtures.google_disabled = True
    try:
        failed_workflow = await dispatch_source(
            SourceWork(str(connection_id), str(user_id), str(workspace_id), "gmail"),
            settings=settings,
            request_id=str(uuid4()),
        )
        try:
            await asyncio.wait_for(client.get_workflow_handle(failed_workflow).result(), 120)
        except WorkflowFailureError:
            pass
        else:
            raise AssertionError("Disabled fixture Google API must fail the source workflow")
        status = await _describe_sync(failed_workflow, settings.temporal_target)
        assert status.status == "failed"
        assert status.commitment_count is None
        assert status.error == {"code": "google_api_disabled", "http_status": 403}
        assert "synthetic-private" not in status.model_dump_json()
        assert fixtures.reads == reads_before_failure + 3
        assert fixtures.extractions == extractions_before_failure
        async with sessions() as database:
            failures = list(
                await database.scalars(
                    select(AuditEvent).where(
                        AuditEvent.workspace_id == workspace_id,
                        AuditEvent.event_type == "intelligence.source.failed",
                    )
                )
            )
            assert len(failures) == 3
            assert all(
                failure.event_metadata["error_diagnostic"] == status.error
                and "synthetic-private" not in json.dumps(failure.event_metadata)
                for failure in failures
            )
    finally:
        fixtures.google_disabled = False
    # Hold the provider response until a second real Temporal activity hits the
    # durable attempt fence. No row lock is held over I/O, no duplicate provider
    # read occurs, and local contention must not create an upstream cooldown.
    reads_before_quota = fixtures.reads
    fixtures.google_quota_limited = True
    fixtures.quota_started = asyncio.Event()
    fixtures.quota_release = asyncio.Event()

    async def dispatch_quota() -> str:
        return await dispatch_source(
            SourceWork(str(connection_id), str(user_id), str(workspace_id), "gmail"),
            settings=settings,
            request_id=str(uuid4()),
        )

    try:
        quota_workflows = [await dispatch_quota()]
        try:
            await asyncio.wait_for(fixtures.quota_started.wait(), timeout=15)
            before = await source_fence_snapshot(sessions, connection_id)
            fixtures.busy_started = asyncio.Event()
            quota_workflows.append(await dispatch_quota())
            await wait_for_source_deferral(sessions, connection_id, fixtures, before)
            assert fixtures.reads == reads_before_quota + 1
        finally:
            fixtures.quota_release.set()
        for attempt in range(3):
            quota_workflow = quota_workflows[attempt] if attempt < 2 else await dispatch_quota()
            try:
                await asyncio.wait_for(client.get_workflow_handle(quota_workflow).result(), 30)
            except WorkflowFailureError:
                pass
            else:
                raise AssertionError("Quota fixture must fail without retrying Google")
            status = await _describe_sync(quota_workflow, settings.temporal_target)
            assert status.status == "failed"
            assert status.error is not None
            assert status.error["code"] == "google_daily_limit_exceeded"
            assert 0 < int(status.error["retry_after_seconds"]) <= 300
            assert fixtures.reads == reads_before_quota + 1
            async with sessions() as database:
                cooldown = await source_cooldown(database, connection_id, "gmail")
                assert cooldown is not None
                assert await source_cooldown(database, connection_id, "calendar") is None
                quotas = list(
                    await database.scalars(
                        select(AuditEvent).where(
                            AuditEvent.workspace_id == workspace_id,
                            AuditEvent.event_type == "intelligence.source.failed",
                            AuditEvent.event_metadata["error_diagnostic"]["code"].as_string()
                            == "google_daily_limit_exceeded",
                        )
                    )
                )
                assert len(quotas) == 1
                assert "synthetic-private" not in json.dumps(quotas[0].event_metadata)
                if attempt == 0:
                    retry_not_before = quotas[0].event_metadata["retry_not_before"]
                else:
                    assert quotas[0].event_metadata["retry_not_before"] == retry_not_before
    finally:
        fixtures.quota_release.set()
        fixtures.quota_started = fixtures.quota_release = None
        fixtures.google_quota_limited = False
    async with sessions() as database:
        item = await database.get(Commitment, target.id)
        assert item is not None and item.attention_score == target.score + 1
        user = await database.get(User, user_id)
        assert user is not None
        user.agent_paused = True
        await database.commit()
    reads = fixtures.reads
    assert await process("gmail") == 0
    assert fixtures.reads == reads
    async with sessions() as database:
        user = await database.get(User, user_id)
        connection = await database.get(Connection, connection_id)
        assert user is not None and connection is not None
        user.agent_paused = False
        connection.status = "revoked"
        connection.granted_scopes = []
        await database.commit()
    assert await process("calendar") == 0
    assert fixtures.reads == reads
    async with sessions() as database:
        for action_model in (Action, Approval):
            assert (
                await database.scalar(
                    select(func.count())
                    .select_from(action_model)
                    .where(action_model.workspace_id == workspace_id)
                )
                == 0
            )


async def verify_gmail_resume(
    sessions: async_sessionmaker[AsyncSession],
    client: Client,
    settings: Settings,
    fixtures: ProviderFixtures,
) -> None:
    """A late quota failure preserves chronological progress across real jobs."""
    user_id, workspace_id = uuid4(), uuid4()
    connection_id, _ = await seed(sessions, user_id, workspace_id)
    fixtures.resume_enabled = True
    fixtures.resume_quota_limited = True
    extractions_before = fixtures.extractions

    async def dispatch() -> str:
        return await dispatch_source(
            SourceWork(str(connection_id), str(user_id), str(workspace_id), "gmail"),
            settings=settings,
            request_id=str(uuid4()),
        )

    try:
        # Start a genuine bootstrap. Its captured history cursor must remain
        # unpublished until every listed message has a durable outcome.
        async with sessions() as database:
            await database.execute(
                delete(IntelligenceCursor).where(
                    IntelligenceCursor.connection_id == connection_id,
                    IntelligenceCursor.source == "gmail",
                )
            )
            await database.commit()
        failed_workflow = await dispatch()
        try:
            await asyncio.wait_for(client.get_workflow_handle(failed_workflow).result(), 60)
        except WorkflowFailureError:
            pass
        else:
            raise AssertionError("Partial Gmail quota fixture must stop before the last message")
        status = await _describe_sync(failed_workflow, settings.temporal_target)
        assert status.status == "failed"
        assert status.error is not None
        assert status.error["code"] == "google_daily_limit_exceeded"
        assert fixtures.extractions == extractions_before + 1
        async with sessions() as database:
            plan = await database.scalar(
                select(GmailSyncPlan).where(GmailSyncPlan.connection_id == connection_id)
            )
            assert plan is not None and plan.phase == "process" and plan.position == 1
            assert plan.initial_cursor is None and plan.cursor == "201"
            assert [entry["id"] for entry in plan.entries] == [
                "resume-1",
                "resume-2",
                "resume-3",
            ]
            assert (
                await database.scalar(
                    select(IntelligenceCursor).where(
                        IntelligenceCursor.connection_id == connection_id,
                        IntelligenceCursor.source == "gmail",
                    )
                )
                is None
            )
            receipts = list(
                await database.scalars(
                    select(IntelligenceSourceReceipt).where(
                        IntelligenceSourceReceipt.connection_id == connection_id
                    )
                )
            )
            assert len(receipts) == 1 and receipts[0].external_id == "resume-1"
            first_receipt_id = receipts[0].id
            failures = list(
                await database.scalars(
                    select(AuditEvent).where(
                        AuditEvent.workspace_id == workspace_id,
                        AuditEvent.event_type == "intelligence.source.failed",
                    )
                )
            )
            assert len(failures) == 1
            assert await source_cooldown(database, connection_id, "gmail") is not None
            # This fixture returns a non-retryable daily quota error. Its durable
            # cooldown is the source audit, not a fabricated transient-provider
            # retry deadline. Expire only this isolated synthetic audit deadline;
            # the quota gate above verifies real retries cannot bypass/slide it.
            failures[0].event_metadata = {
                **failures[0].event_metadata,
                "retry_not_before": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            }
            await database.commit()
        fixtures.resume_quota_limited = False
        fixtures.resume_started = asyncio.Event()
        fixtures.resume_release = asyncio.Event()
        workflows = [await dispatch()]
        try:
            await asyncio.wait_for(fixtures.resume_started.wait(), timeout=15)
            before = await source_fence_snapshot(sessions, connection_id)
            fixtures.busy_started = asyncio.Event()
            reads_before_competitor = fixtures.resume_reads.copy()
            workflows.append(await dispatch())
            await wait_for_source_deferral(sessions, connection_id, fixtures, before)
            assert fixtures.resume_reads == reads_before_competitor
        finally:
            fixtures.resume_release.set()
        for workflow_id in workflows:
            result = await asyncio.wait_for(client.get_workflow_handle(workflow_id).result(), 60)
            assert isinstance(result, int)
            status = await _describe_sync(workflow_id, settings.temporal_target)
            assert status.status == "completed" and status.error is None
        assert fixtures.extractions == extractions_before + 3
        assert fixtures.resume_reads["profile"] == 1
        assert fixtures.resume_reads["list"] == 1
        assert fixtures.resume_reads["history"] <= 1
        for position in (1, 2, 3):
            assert fixtures.resume_reads[f"metadata:resume-{position}"] == 1
            assert fixtures.resume_reads[f"full:resume-{position}"] == (2 if position == 2 else 1)
        assert fixtures.resume_full_order == ["resume-1", "resume-2", "resume-2", "resume-3"]
        async with sessions() as database:
            assert (
                await database.scalar(
                    select(GmailSyncPlan).where(GmailSyncPlan.connection_id == connection_id)
                )
                is None
            )
            cursor = await database.scalar(
                select(IntelligenceCursor).where(
                    IntelligenceCursor.connection_id == connection_id,
                    IntelligenceCursor.source == "gmail",
                )
            )
            assert cursor is not None and cursor.cursor == "201"
            receipts = list(
                await database.scalars(
                    select(IntelligenceSourceReceipt).where(
                        IntelligenceSourceReceipt.connection_id == connection_id
                    )
                )
            )
            assert len(receipts) == 3
            assert {receipt.external_id for receipt in receipts} == {
                "resume-1",
                "resume-2",
                "resume-3",
            }
            assert next(
                receipt for receipt in receipts if receipt.external_id == "resume-1"
            ).id == (first_receipt_id)
            assert all(receipt.outcome == "processed" for receipt in receipts)
    finally:
        if fixtures.resume_release is not None:
            fixtures.resume_release.set()
        fixtures.resume_enabled = fixtures.resume_quota_limited = False
        fixtures.resume_started = fixtures.resume_release = None
        async with sessions() as database:
            await database.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await database.execute(delete(User).where(User.id == user_id))
            await database.commit()


async def verify_proactive_concurrency(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Lifecycle activities and the briefing request may arrive simultaneously."""
    user_id, workspace_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    try:
        async with sessions() as database:
            database.add_all(
                [
                    User(id=user_id, email=f"proactive-ci-{user_id}@example.test"),
                    Workspace(id=workspace_id, name="Isolated proactive concurrency fixture"),
                ]
            )
            await database.flush()
            for kind in ("deadline", "meeting", "promise"):
                database.add(
                    Commitment(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        commitment_type=kind,
                        title=f"Synthetic {kind}",
                        dedupe_key=f"proactive-concurrency-{kind}",
                        status="confirmed",
                        priority=5,
                        due_at=now + timedelta(hours=1),
                    )
                )
            await database.commit()

        async def concurrently_evaluate() -> list[list[ProactiveSignal]]:
            start = asyncio.Event()

            async def evaluate() -> list[ProactiveSignal]:
                async with sessions() as database:
                    await start.wait()
                    return await evaluate_workspace(
                        database,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        timezone_name="UTC",
                        now=now,
                    )

            tasks = [asyncio.create_task(evaluate()) for _ in range(8)]
            start.set()
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=30
            )
            errors = [result for result in results if isinstance(result, BaseException)]
            assert not errors, f"Concurrent proactive evaluation failed: {errors!r}"
            return [result for result in results if isinstance(result, list)]

        # Starting without preferences also exercises concurrent first use.
        results = await concurrently_evaluate()
        signal_ids = {signal.id for signal in results[0]}
        assert len(signal_ids) == 3
        assert all({signal.id for signal in result} == signal_ids for result in results)
        dismissed_id, snoozed_id = sorted(signal_ids)[:2]
        snoozed_until = now + timedelta(hours=4)
        async with sessions() as database:
            dismissed = await database.get(ProactiveSignal, dismissed_id)
            snoozed = await database.get(ProactiveSignal, snoozed_id)
            assert dismissed is not None and snoozed is not None
            dismissed.status = "dismissed"
            dismissed.dismissed_at = now
            dismissed.tier = "suppressed"
            snoozed.snoozed_until = snoozed_until
            snoozed.tier = "suppressed"
            await database.commit()
        results = await concurrently_evaluate()
        for result in results:
            by_id = {signal.id: signal for signal in result}
            assert by_id[dismissed_id].status == "dismissed"
            assert by_id[dismissed_id].tier == "suppressed"
            saved_until = by_id[snoozed_id].snoozed_until
            assert saved_until is not None and aware(saved_until) == snoozed_until
            assert by_id[snoozed_id].tier == "suppressed"
        async with sessions() as database:
            for model, expected in ((ProactiveSignal, 3), (ProactivePreference, 1)):
                assert (
                    await database.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.workspace_id == workspace_id)
                    )
                    == expected
                )
            assert (
                await database.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.workspace_id == workspace_id,
                        AuditEvent.event_type == "proactive.signal.created",
                    )
                )
                == 3
            )
    finally:
        async with sessions() as database:
            await database.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await database.execute(delete(User).where(User.id == user_id))
            await database.commit()


async def run() -> None:
    settings = IntegrationSettings(
        ai_provider="openai",
        openai_api_key=SecretStr("synthetic-key-never-sent"),
        temporal_task_queue=f"navox-intelligence-ci-{uuid4()}",
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")
    engine = create_async_engine(
        settings.database_url,
        connect_args={"server_settings": {"application_name": f"navox-intelligence-ci-{uuid4()}"}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixtures = ProviderFixtures()
    user_id, workspace_id = uuid4(), uuid4()
    try:
        await verify_proactive_concurrency(sessions)
        connection_id, events = await seed(sessions, user_id, workspace_id)
        client = await Client.connect(settings.temporal_target)

        async def observe_processing(database: AsyncSession, **kwargs: Any) -> list[UUID]:
            try:
                return await actual_process_connection(database, **kwargs)
            except GoogleSourceBusyError:
                fixtures.busy_deferrals += 1
                fixtures.busy_connection_id = kwargs["connection_id"]
                if fixtures.busy_started is not None:
                    fixtures.busy_started.set()
                raise

        with (
            patch("navox.intelligence.ingestion.process_connection", observe_processing),
            patch("navox.intelligence.activities.get_settings", return_value=settings),
            patch("navox.intelligence.activities.get_session_factory", return_value=sessions),
            patch(
                "navox.intelligence.activities.build_ai_gateway", return_value=AIGateway(fixtures)
            ),
            patch(
                "navox.intelligence.ingestion.access_token_for_connection",
                AsyncMock(return_value="synthetic-access-token"),
            ),
            patch(
                "navox.intelligence.ingestion.GoogleSourceGateway",
                side_effect=lambda: GoogleSourceGateway(
                    transport=httpx.MockTransport(fixtures.google)
                ),
            ),
            patch(
                "httpx.AsyncHTTPTransport.handle_async_request",
                AsyncMock(
                    side_effect=AssertionError("Real HTTP forbidden in integration fixtures")
                ),
            ),
        ):
            async with Worker(
                client,
                task_queue=settings.temporal_task_queue,
                workflows=[
                    ProcessSourceEventWorkflow,
                    TodayRefreshWorkflow,
                    FeedbackLearningWorkflow,
                    AttentionEvaluationWorkflow,
                ],
                activities=[process_source_activity, refresh_intelligence_activity],
            ):
                await verify(
                    sessions,
                    client,
                    settings,
                    fixtures,
                    user_id,
                    workspace_id,
                    connection_id,
                    events,
                )
                await verify_gmail_resume(sessions, client, settings, fixtures)
                await verify_gmail_recheck_concurrency(sessions, settings)
                assert fixtures.busy_deferrals >= 2
        print(
            json.dumps(
                {
                    "passed": True,
                    "mode": "compose_provider_fixture",
                    "sources": 2,
                    "replay_verified": True,
                    "feedback_verified": True,
                    "pause_and_revocation_verified": True,
                    "sync_completion_status_verified": True,
                    "sync_failure_diagnostic_verified": True,
                    "quota_cooldown_verified": True,
                    "quota_concurrency_verified": True,
                    "runtime_fence_concurrency_verified": fixtures.busy_deferrals >= 2,
                    "gmail_partial_resume_verified": True,
                    "gmail_resume_concurrency_verified": True,
                    "proactive_concurrency_verified": True,
                    "gmail_recheck_concurrency_verified": True,
                    "live_provider_quality_measured": False,
                }
            )
        )
    finally:
        async with sessions() as database:
            await database.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await database.execute(delete(User).where(User.id == user_id))
            await database.commit()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(run(), timeout=300))
