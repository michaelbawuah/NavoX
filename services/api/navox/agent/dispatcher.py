from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from navox.agent.audit import add_audit_event
from navox.core.settings import Settings
from navox.db.models import Plan, WorkflowRef
from navox.workflows.handle_commitment import HandleCommitmentInput, HandleCommitmentWorkflow


class AgentDispatchError(RuntimeError):
    pass


class TemporalAgentDispatcher:
    async def dispatch(
        self,
        database: AsyncSession,
        *,
        plan: Plan,
        step_ids: list[UUID],
        settings: Settings,
    ) -> WorkflowRef:
        existing = await database.scalar(
            select(WorkflowRef).where(
                WorkflowRef.entity_type == "plan",
                WorkflowRef.entity_id == plan.id,
                WorkflowRef.workflow_type == "handle_commitment",
            )
        )
        workflow_id = f"navox-handle-plan-{plan.id}"
        if existing is None:
            existing = WorkflowRef(
                user_id=plan.user_id,
                workspace_id=plan.workspace_id,
                entity_type="plan",
                entity_id=plan.id,
                workflow_type="handle_commitment",
                temporal_workflow_id=workflow_id,
                status="pending",
            )
            database.add(existing)
            await database.commit()

        if existing.status in {"running", "completed", "blocked", "failed"}:
            return existing

        client = await Client.connect(settings.temporal_target)
        try:
            await client.start_workflow(
                HandleCommitmentWorkflow.run,
                HandleCommitmentInput(
                    plan_id=str(plan.id),
                    step_ids=[str(step_id) for step_id in step_ids],
                ),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
                execution_timeout=timedelta(minutes=5),
            )
        except Exception as start_error:
            handle = client.get_workflow_handle(workflow_id=workflow_id)
            try:
                await handle.describe()
            except Exception:
                existing.status = "dispatch_failed"
                plan.status = "dispatch_failed"
                plan.error_code = "temporal_dispatch_failed"
                add_audit_event(
                    database,
                    user_id=plan.user_id,
                    workspace_id=plan.workspace_id,
                    event_type="agent.workflow.dispatch_failed",
                    entity_type="plan",
                    entity_id=plan.id,
                    metadata={"workflow_id": workflow_id},
                )
                await database.commit()
                raise AgentDispatchError("Temporal workflow dispatch failed") from start_error

        existing.status = "running"
        plan.status = "running"
        plan.error_code = None
        add_audit_event(
            database,
            user_id=plan.user_id,
            workspace_id=plan.workspace_id,
            event_type="agent.workflow.dispatched",
            entity_type="plan",
            entity_id=plan.id,
            metadata={"workflow_id": workflow_id, "step_count": len(step_ids)},
        )
        await database.commit()
        return existing
