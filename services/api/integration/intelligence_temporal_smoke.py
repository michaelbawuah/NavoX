"""Compose gate with real PostgreSQL/Temporal and synthetic Google/model I/O.

No production service imports this harness. Provider patches exist only in this
process; the task queue and database workspace are unique to each run.
"""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.client import Client
from temporalio.worker import Worker

from navox.ai.gateway import AIGateway, StructuredOutputResponse
from navox.core.settings import Settings
from navox.db.models import (
    Action,
    Approval,
    AuditEvent,
    Commitment,
    Connection,
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
from navox.intelligence.jobs import SourceWork, WorkspaceWork
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

    def google(self, request: httpx.Request) -> httpx.Response:
        self.reads += 1
        assert request.headers["authorization"] == "Bearer synthetic-access-token"
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

    async def generate_json(
        self, *, schema_name: str, schema: dict[str, Any], instructions: str, input_text: str
    ) -> StructuredOutputResponse:
        self.extractions += 1
        data = json.loads(input_text)
        content = str(data["content"])
        meeting = data["source_type"] == "calendar_event"
        return StructuredOutputResponse(
            data={
                "schema_version": "operational-extraction.v1",
                "observations": [
                    {
                        "observation_type": "meeting" if meeting else "request",
                        "action_text": "attend" if meeting else "send",
                        "object_text": "the research review" if meeting else "the budget",
                        "confidence": 0.97,
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
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fixtures = ProviderFixtures()
    user_id, workspace_id = uuid4(), uuid4()
    try:
        await verify_proactive_concurrency(sessions)
        connection_id, events = await seed(sessions, user_id, workspace_id)
        client = await Client.connect(settings.temporal_target)
        with (
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
        print(
            json.dumps(
                {
                    "passed": True,
                    "mode": "compose_provider_fixture",
                    "sources": 2,
                    "replay_verified": True,
                    "feedback_verified": True,
                    "pause_and_revocation_verified": True,
                    "proactive_concurrency_verified": True,
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
