import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api.main import app
from navox.commitments.engine import CommitmentEngine
from navox.commitments.policy import CommitmentConfidencePolicy
from navox.commitments.schema import CommitmentExtraction
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import (
    Commitment,
    CommitmentRelation,
    CommitmentSource,
    Connection,
    IncomingEvent,
)
from navox.db.session import get_database_session
from navox.events.processor import IncomingEventProcessor

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest_asyncio.fixture
async def commitment_test_environment() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, session_factory, settings
    app.dependency_overrides.pop(get_database_session, None)
    app.dependency_overrides.pop(get_settings, None)
    await engine.dispose()


async def register(client: AsyncClient, email: str = "owner@example.com") -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "twelve-character-password",
            "display_name": "Owner",
        },
    )
    assert response.status_code == 201
    return response.json()


async def seed_processed_event(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_event_id: str,
) -> UUID:
    account = await register(client)
    workspace = account["workspace"]
    assert isinstance(workspace, dict)
    user_id = UUID(str(account["id"]))
    workspace_id = UUID(str(workspace["id"]))
    async with session_factory() as session:
        connection = Connection(
            user_id=user_id,
            workspace_id=workspace_id,
            provider="google",
            external_account_id="google-account-123",
            external_email="connected@example.com",
            status="active",
            granted_scopes=["openid"],
        )
        session.add(connection)
        await session.flush()
        event = IncomingEvent(
            connection_id=connection.id,
            user_id=user_id,
            workspace_id=workspace_id,
            provider="google",
            source="gmail",
            event_type="google.gmail.history.changed",
            external_event_id=external_event_id,
            external_resource_id=f"history-{external_event_id}",
            payload_hash="a" * 64,
            event_metadata={"history_id": external_event_id},
            status="received",
            occurred_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        )
        IncomingEventProcessor().process(event)
        session.add(event)
        await session.commit()
        return event.id


def engine_for(settings: Settings) -> CommitmentEngine:
    return CommitmentEngine(
        CommitmentConfidencePolicy(
            moderate_threshold=settings.commitment_moderate_confidence_threshold,
            high_threshold=settings.commitment_high_confidence_threshold,
        )
    )


@pytest.mark.asyncio
async def test_engine_creates_confirmed_and_candidate_commitments_with_provenance_and_relations(
    commitment_test_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = commitment_test_environment
    event_id = await seed_processed_event(client, session_factory, external_event_id="event-1")
    payload = {
        "candidates": [
            {
                "type": "deadline",
                "title": "Submit systems design",
                "description": "Required before the engineering review.",
                "priority": 5,
                "due_at": "2026-10-01T17:00:00Z",
                "confidence": 0.94,
            },
            {
                "type": "follow_up",
                "title": "Confirm the review attendees",
                "confidence": 0.72,
            },
            {
                "type": "task",
                "title": "Maybe read a related article",
                "confidence": 0.24,
            },
        ],
        "relations": [{"from_index": 1, "to_index": 0, "relation_type": "depends_on"}],
    }

    async with session_factory() as session:
        result = await engine_for(settings).process_model_output(session, event_id, payload)
        commitments = list(await session.scalars(select(Commitment).order_by(Commitment.title)))
        sources = list(await session.scalars(select(CommitmentSource)))
        relations = list(await session.scalars(select(CommitmentRelation)))

    assert result.created_count == 2
    assert result.deduplicated_count == 0
    assert result.suppressed_count == 1
    assert result.relation_count == 1
    assert {commitment.status for commitment in commitments} == {"candidate", "confirmed"}
    assert len(sources) == 2
    assert all(source.incoming_event_id == event_id for source in sources)
    assert all(
        source.source_metadata == {"event_type": "google.gmail.history.changed"}
        for source in sources
    )
    assert len(relations) == 1

    response = await client.get("/api/v1/commitments")
    assert response.status_code == 200
    listed = response.json()
    assert {item["status"] for item in listed} == {"candidate", "confirmed"}


@pytest.mark.asyncio
async def test_engine_deduplicates_in_workspace_and_adds_a_second_provenance_source(
    commitment_test_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = commitment_test_environment
    first_event_id = await seed_processed_event(
        client,
        session_factory,
        external_event_id="event-1",
    )
    payload = {
        "candidates": [
            {
                "type": "renewal",
                "title": "Renew professional insurance",
                "due_at": "2026-11-01T09:00:00Z",
                "confidence": 0.9,
            }
        ]
    }

    async with session_factory() as session:
        first = await engine_for(settings).process_model_output(session, first_event_id, payload)
    assert first.created_count == 1

    async with session_factory() as session:
        first_event = await session.get(IncomingEvent, first_event_id)
        assert first_event is not None
        second_event = IncomingEvent(
            connection_id=first_event.connection_id,
            user_id=first_event.user_id,
            workspace_id=first_event.workspace_id,
            provider="google",
            source="calendar",
            event_type="google.calendar.notification",
            external_event_id="event-2",
            external_resource_id="calendar-event-2",
            payload_hash="b" * 64,
            event_metadata={"message_number": "2"},
            status="processed",
            occurred_at=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
            processed_at=datetime(2026, 9, 23, 12, 1, tzinfo=UTC),
        )
        session.add(second_event)
        await session.commit()
        second_event_id = second_event.id

    async with session_factory() as session:
        second = await engine_for(settings).process_model_output(session, second_event_id, payload)
        commitments = list(await session.scalars(select(Commitment)))
        sources = list(await session.scalars(select(CommitmentSource)))

    assert second.created_count == 0
    assert second.deduplicated_count == 1
    assert len(commitments) == 1
    assert {source.incoming_event_id for source in sources} == {first_event_id, second_event_id}


@pytest.mark.asyncio
async def test_candidate_review_is_workspace_scoped_and_cannot_revert_a_confirmation(
    commitment_test_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = commitment_test_environment
    event_id = await seed_processed_event(client, session_factory, external_event_id="event-1")
    payload = {
        "candidates": [
            {
                "type": "follow_up",
                "title": "Confirm delivery details",
                "confidence": 0.7,
            }
        ]
    }
    async with session_factory() as session:
        result = await engine_for(settings).process_model_output(session, event_id, payload)
    commitment_id = result.commitment_ids[0]

    confirmed = await client.post(f"/api/v1/commitments/{commitment_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    rejected = await client.post(f"/api/v1/commitments/{commitment_id}/reject")
    assert rejected.status_code == 409

    other_account = await register(client, email="other@example.com")
    assert other_account["id"]
    hidden = await client.get(f"/api/v1/commitments/{commitment_id}")
    assert hidden.status_code == 404


@pytest.mark.parametrize(
    "scenario",
    json.loads((REPOSITORY_ROOT / "evals/commitments/malicious_outputs.json").read_text()),
    ids=lambda scenario: str(scenario["name"]),
)
def test_malicious_structured_outputs_fail_closed(scenario: dict[str, object]) -> None:
    model_output = scenario["model_output"]
    assert isinstance(model_output, dict)
    with pytest.raises(ValidationError):
        CommitmentExtraction.model_validate(model_output)


@pytest.mark.parametrize(
    "scenario",
    json.loads((REPOSITORY_ROOT / "evals/commitments/false_positive_outputs.json").read_text()),
    ids=lambda scenario: str(scenario["name"]),
)
def test_false_positive_outputs_are_suppressed_by_confidence_policy(
    scenario: dict[str, object],
) -> None:
    model_output = scenario["model_output"]
    assert isinstance(model_output, dict)
    extraction = CommitmentExtraction.model_validate(model_output)
    policy = CommitmentConfidencePolicy(moderate_threshold=0.65, high_threshold=0.85)

    assert all(policy.status_for(candidate) is None for candidate in extraction.candidates)
