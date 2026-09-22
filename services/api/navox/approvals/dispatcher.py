from datetime import timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from navox.agent.audit import add_audit_event
from navox.core.settings import Settings
from navox.db.models import Action, WorkflowRef
from navox.workflows.approved_action import ApprovedActionInput, ApprovedActionWorkflow


class ApprovalDispatchError(RuntimeError):
    pass


class ApprovalDispatcher(Protocol):
    async def ensure_started(
        self,
        database: AsyncSession,
        *,
        action: Action,
        settings: Settings,
    ) -> WorkflowRef: ...

    async def signal(
        self,
        *,
        action_id: UUID,
        decision: str,
        settings: Settings,
    ) -> None: ...


class TemporalApprovalDispatcher:
    async def ensure_started(
        self,
        database: AsyncSession,
        *,
        action: Action,
        settings: Settings,
    ) -> WorkflowRef:
        existing = await database.scalar(
            select(WorkflowRef).where(
                WorkflowRef.entity_type == "action",
                WorkflowRef.entity_id == action.id,
                WorkflowRef.workflow_type == "approved_action",
            )
        )
        workflow_id = f"navox-approved-action-{action.id}"
        if existing is None:
            existing = WorkflowRef(
                user_id=action.user_id,
                workspace_id=action.workspace_id,
                entity_type="action",
                entity_id=action.id,
                workflow_type="approved_action",
                temporal_workflow_id=workflow_id,
                status="pending",
            )
            database.add(existing)
            await database.commit()

        if existing.status in {"running", "completed", "manual_review", "rejected", "expired"}:
            return existing

        client: Client | None = None
        try:
            client = await Client.connect(settings.temporal_target)
            await client.start_workflow(
                ApprovedActionWorkflow.run,
                ApprovedActionInput(action_id=str(action.id)),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
                execution_timeout=timedelta(hours=24),
            )
        except Exception as start_error:
            already_exists = False
            if client is not None:
                handle = client.get_workflow_handle(workflow_id=workflow_id)
                try:
                    await handle.describe()
                    already_exists = True
                except Exception:
                    already_exists = False
            if not already_exists:
                existing.status = "dispatch_failed"
                add_audit_event(
                    database,
                    user_id=action.user_id,
                    workspace_id=action.workspace_id,
                    event_type="approval.workflow.dispatch_failed",
                    entity_type="action",
                    entity_id=action.id,
                    metadata={"workflow_id": workflow_id},
                )
                await database.commit()
                raise ApprovalDispatchError("Approval workflow dispatch failed") from start_error

        existing.status = "running"
        add_audit_event(
            database,
            user_id=action.user_id,
            workspace_id=action.workspace_id,
            event_type="approval.workflow.dispatched",
            entity_type="action",
            entity_id=action.id,
            metadata={"workflow_id": workflow_id},
        )
        await database.commit()
        return existing

    async def signal(
        self,
        *,
        action_id: UUID,
        decision: str,
        settings: Settings,
    ) -> None:
        try:
            client = await Client.connect(settings.temporal_target)
            handle = client.get_workflow_handle(workflow_id=f"navox-approved-action-{action_id}")
            await handle.signal("approval_decision", decision)
        except Exception as error:
            raise ApprovalDispatchError("Approval workflow signal failed") from error
