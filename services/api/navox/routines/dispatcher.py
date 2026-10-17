import asyncio
from typing import Protocol

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from navox.core.settings import Settings
from navox.routines.activities import RoutineTickInput

RECONCILIATION_WORKFLOW_ID = "navox-personal-routine-reconciliation-v1"


class RoutineDispatcher(Protocol):
    async def start(self, payload: RoutineTickInput, settings: Settings) -> None: ...


class TemporalRoutineDispatcher:
    async def start(self, payload: RoutineTickInput, settings: Settings) -> None:
        from navox.workflows.routines import PersonalRoutineWorkflow

        async with asyncio.timeout(8):
            client = await Client.connect(settings.temporal_target)
            try:
                await client.start_workflow(
                    PersonalRoutineWorkflow.run,
                    payload,
                    id=f"navox-personal-routine-{payload.routine_id}-v{payload.revision}",
                    task_queue=settings.temporal_task_queue,
                    # The DB opt-in and revision remain authoritative. Reusing a
                    # closed run recovers timeouts/crashes; a live run is unique.
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                )
            except WorkflowAlreadyStartedError:
                pass
