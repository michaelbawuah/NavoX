from uuid import UUID

from temporalio import activity

from navox.agent.executor import execute_plan_step, finalize_plan
from navox.db.session import get_session_factory


@activity.defn
async def execute_plan_step_activity(step_id: str) -> str:
    async with get_session_factory()() as database:
        return await execute_plan_step(database, UUID(step_id))


@activity.defn
async def finalize_plan_activity(plan_id: str) -> str:
    async with get_session_factory()() as database:
        return await finalize_plan(database, UUID(plan_id))
