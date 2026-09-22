from uuid import UUID

from temporalio import activity

from navox.approvals.execution import (
    action_authorization_state,
    execute_approved_gmail_send,
    mark_execution_uncertain,
)
from navox.core.settings import get_settings
from navox.db.session import get_session_factory


@activity.defn
async def action_authorization_state_activity(action_id: str) -> str:
    async with get_session_factory()() as database:
        return await action_authorization_state(database, UUID(action_id))


@activity.defn
async def execute_approved_action_activity(action_id: str) -> str:
    async with get_session_factory()() as database:
        return await execute_approved_gmail_send(
            database,
            action_id=UUID(action_id),
            settings=get_settings(),
        )


@activity.defn
async def mark_execution_uncertain_activity(action_id: str) -> str:
    async with get_session_factory()() as database:
        return await mark_execution_uncertain(
            database,
            action_id=UUID(action_id),
            reason="approved_action_activity_failed_after_execution_start",
        )
