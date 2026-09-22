from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.agent.contracts import get_action_contract
from navox.agent.dispatcher import AgentDispatcher
from navox.agent.executor import execute_plan_step, finalize_plan
from navox.agent.policy import ActionPolicy
from navox.agent.service import BoundedAgentService
from navox.api.agent import get_agent_dispatcher
from navox.api.main import app
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import Action, AuditEvent, Plan, WorkflowRef
from navox.db.session import get_database_session


class InlineDispatcher(AgentDispatcher):
    async def dispatch(
        self,
        database: AsyncSession,
        *,
        plan: Plan,
        step_ids: list[UUID],
        settings: Settings,
    ) -> WorkflowRef:
        del settings
        workflow_ref = await database.scalar(
            select(WorkflowRef).where(
                WorkflowRef.entity_type == "plan",
                WorkflowRef.entity_id == plan.id,
                WorkflowRef.workflow_type == "handle_commitment",
            )
        )
        if workflow_ref is None:
            workflow_ref = WorkflowRef(
                user_id=plan.user_id,
                workspace_id=plan.workspace_id,
                entity_type="plan",
                entity_id=plan.id,
                workflow_type="handle_commitment",
                temporal_workflow_id=f"test-handle-{plan.id}",
                status="running",
            )
            database.add(workflow_ref)
        plan.status = "running"
        await database.commit()

        for step_id in step_ids:
            result = await execute_plan_step(database, step_id)
            if result != "completed":
                break
        await finalize_plan(database, plan.id)
        await database.refresh(workflow_ref)
        return workflow_ref


@pytest_asyncio.fixture
async def agent_environment() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(app_environment="test")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_agent_dispatcher] = lambda: InlineDispatcher()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, session_factory, settings
    app.dependency_overrides.pop(get_database_session, None)
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_agent_dispatcher, None)
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


async def create_commitment(
    client: AsyncClient, title: str = "Ship bounded agent"
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/commitments",
        json={
            "request_id": str(uuid4()),
            "type": "task",
            "title": title,
            "priority": 5,
            "due_at": None,
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_handle_is_idempotent_persisted_and_limited_to_safe_r0_r1(
    agent_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, _ = agent_environment
    await register(client)
    commitment = await create_commitment(client)
    request_id = uuid4()

    first = await client.post(
        f"/api/v1/commitments/{commitment['id']}/handle",
        json={"request_id": str(request_id), "goal": "Safely prepare the next move"},
    )
    assert first.status_code == 202
    body = first.json()
    assert body["status"] == "completed"
    assert len(body["steps"]) == 3
    assert [step["risk_level"] for step in body["steps"]] == ["R0", "R1", "R1"]
    assert all(step["status"] == "completed" for step in body["steps"])
    assert all(step["action"]["provider"] == "navox" for step in body["steps"])
    assert all(step["action"]["requires_approval"] is False for step in body["steps"])
    assert body["steps"][-1]["output"]["external_actions_executed"] is False

    repeated = await client.post(
        f"/api/v1/commitments/{commitment['id']}/handle",
        json={"request_id": str(request_id), "goal": "Safely prepare the next move"},
    )
    assert repeated.status_code == 202
    assert repeated.json()["id"] == body["id"]

    plans = await client.get("/api/v1/plans", params={"commitment_id": commitment["id"]})
    assert plans.status_code == 200
    assert [plan["id"] for plan in plans.json()] == [body["id"]]

    async with session_factory() as session:
        plan_count = len(list(await session.scalars(select(Plan))))
        action_count = len(list(await session.scalars(select(Action))))
        audit_types = {
            event.event_type for event in await session.scalars(select(AuditEvent))
        }
    assert plan_count == 1
    assert action_count == 3
    assert "agent.plan.created" in audit_types
    assert "agent.plan.completed" in audit_types
    assert "agent.action.completed" in audit_types


@pytest.mark.asyncio
async def test_agent_pause_and_terminal_commitment_fail_closed(
    agent_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, _, _ = agent_environment
    await register(client)
    commitment = await create_commitment(client)

    paused = await client.post("/api/v1/agent/pause")
    assert paused.status_code == 200
    assert paused.json() == {"paused": True}

    blocked = await client.post(
        f"/api/v1/commitments/{commitment['id']}/handle",
        json={"request_id": str(uuid4())},
    )
    assert blocked.status_code == 409
    assert "paused" in blocked.json()["detail"].lower()

    resumed = await client.post("/api/v1/agent/resume")
    assert resumed.status_code == 200
    assert resumed.json() == {"paused": False}

    completed = await client.post(f"/api/v1/commitments/{commitment['id']}/complete")
    assert completed.status_code == 200

    terminal = await client.post(
        f"/api/v1/commitments/{commitment['id']}/handle",
        json={"request_id": str(uuid4())},
    )
    assert terminal.status_code == 409


@pytest.mark.asyncio
async def test_plans_are_strictly_tenant_scoped(
    agent_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, _, _ = agent_environment
    await register(client)
    commitment = await create_commitment(client)
    created = await client.post(
        f"/api/v1/commitments/{commitment['id']}/handle",
        json={"request_id": str(uuid4())},
    )
    assert created.status_code == 202
    plan_id = created.json()["id"]

    await register(client, email="other@example.com")
    hidden = await client.get(f"/api/v1/plans/{plan_id}")
    assert hidden.status_code == 404
    listed = await client.get("/api/v1/plans")
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.asyncio
async def test_executor_rejects_risk_tampering_and_unknown_actions(
    agent_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, _ = agent_environment
    account = await register(client)
    workspace = account["workspace"]
    assert isinstance(workspace, dict)
    commitment = await create_commitment(client)
    user_id = UUID(str(account["id"]))
    workspace_id = UUID(str(workspace["id"]))
    commitment_id = UUID(str(commitment["id"]))

    async with session_factory() as session:
        plan, steps, created = await BoundedAgentService().create_plan(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            commitment_id=commitment_id,
            request_id=uuid4(),
            goal=None,
        )
        assert created is True
        steps[0].risk_level = "R3"
        await session.commit()
        result = await execute_plan_step(session, steps[0].id)
        await session.refresh(plan)
        action = await session.scalar(select(Action).where(Action.plan_step_id == steps[0].id))

    assert result == "blocked"
    assert plan.status == "blocked"
    assert plan.error_code == "risk_contract_mismatch"
    assert action is not None
    assert action.status == "blocked"

    async with session_factory() as session:
        plan, steps, _ = await BoundedAgentService().create_plan(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            commitment_id=commitment_id,
            request_id=uuid4(),
            goal="Prepare another safe plan",
        )
        steps[0].action_type = "provider.unknown_mutation"
        await session.commit()
        result = await execute_plan_step(session, steps[0].id)
        await session.refresh(plan)

    assert result == "blocked"
    assert plan.error_code == "unknown_action_contract"


def test_policy_contracts_keep_provider_actions_fail_closed_in_milestone_5() -> None:
    policy = ActionPolicy()

    gmail_send = get_action_contract("gmail.send")
    assert gmail_send is not None
    send_decision = policy.evaluate(
        gmail_send,
        granted_permissions={"https://www.googleapis.com/auth/gmail.send"},
        agent_paused=False,
    )
    assert send_decision.allowed is True
    assert send_decision.requires_approval is True

    gmail_search = get_action_contract("gmail.search")
    assert gmail_search is not None
    missing = policy.evaluate(gmail_search, granted_permissions=set(), agent_paused=False)
    assert missing.allowed is False
    assert missing.reason.startswith("missing_permissions:")

    unavailable = policy.evaluate(
        gmail_search,
        granted_permissions={"https://www.googleapis.com/auth/gmail.readonly"},
        agent_paused=False,
    )
    assert unavailable.allowed is False
    assert unavailable.reason == "executor_unavailable"
