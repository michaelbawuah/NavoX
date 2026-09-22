from datetime import timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from navox.agent.audit import add_audit_event
from navox.core.settings import Settings
from navox.db.models import Commitment, WorkflowRef
from navox.proactive.activities import ProactiveCommitmentInput, ProactiveWorkspaceInput
from navox.workflows.proactive import (
    CommitmentLifecycleWorkflow,
    DailyBriefingWorkflow,
    FollowUpWorkflow,
    MeetingPreparationWorkflow,
)


class ProactiveDispatchError(RuntimeError):
    pass


class ProactiveDispatcher(Protocol):
    async def activate(
        self,
        database: AsyncSession,
        *,
        user_id: UUID,
        workspace_id: UUID,
        timezone: str,
        settings: Settings,
    ) -> list[WorkflowRef]: ...


def state_suffix(value: str) -> str:
    return sha256(value.encode()).hexdigest()[:12]


class TemporalProactiveDispatcher:
    async def _ensure_started(
        self,
        database: AsyncSession,
        *,
        user_id: UUID,
        workspace_id: UUID,
        entity_type: str,
        entity_id: UUID,
        workflow_type: str,
        workflow_id: str,
        workflow_run: object,
        payload: object,
        settings: Settings,
        execution_timeout: timedelta,
        restart_terminal: bool = False,
    ) -> WorkflowRef:
        existing = await database.scalar(
            select(WorkflowRef).where(
                WorkflowRef.entity_type == entity_type,
                WorkflowRef.entity_id == entity_id,
                WorkflowRef.workflow_type == workflow_type,
            )
        )
        if existing is None:
            existing = WorkflowRef(
                user_id=user_id,
                workspace_id=workspace_id,
                entity_type=entity_type,
                entity_id=entity_id,
                workflow_type=workflow_type,
                temporal_workflow_id=workflow_id,
                status="pending",
            )
            database.add(existing)
            await database.commit()
        elif existing.status == "running" and existing.temporal_workflow_id == workflow_id:
            return existing
        elif not restart_terminal and existing.status in {"completed", "prepared", "missed"}:
            return existing
        else:
            existing.temporal_workflow_id = workflow_id
            existing.status = "pending"
            await database.commit()

        client: Client | None = None
        try:
            client = await Client.connect(settings.temporal_target)
            await client.start_workflow(
                workflow_run,  # type: ignore[arg-type]
                payload,
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
                execution_timeout=execution_timeout,
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
                    user_id=user_id,
                    workspace_id=workspace_id,
                    event_type="proactive.workflow.dispatch_failed",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    metadata={
                        "workflow_id": workflow_id,
                        "workflow_type": workflow_type,
                    },
                )
                await database.commit()
                raise ProactiveDispatchError(
                    f"Temporal proactive workflow dispatch failed: {workflow_type}"
                ) from start_error

        existing.status = "running"
        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="proactive.workflow.dispatched",
            entity_type=entity_type,
            entity_id=entity_id,
            metadata={
                "workflow_id": workflow_id,
                "workflow_type": workflow_type,
            },
        )
        await database.commit()
        return existing

    async def activate(
        self,
        database: AsyncSession,
        *,
        user_id: UUID,
        workspace_id: UUID,
        timezone: str,
        settings: Settings,
    ) -> list[WorkflowRef]:
        refs: list[WorkflowRef] = []
        refs.append(
            await self._ensure_started(
                database,
                user_id=user_id,
                workspace_id=workspace_id,
                entity_type="workspace",
                entity_id=workspace_id,
                workflow_type="daily_briefing",
                workflow_id=f"navox-daily-briefing-{workspace_id}",
                workflow_run=DailyBriefingWorkflow.run,
                payload=ProactiveWorkspaceInput(
                    user_id=str(user_id),
                    workspace_id=str(workspace_id),
                    timezone=timezone,
                ),
                settings=settings,
                execution_timeout=timedelta(days=366),
            )
        )

        commitments = list(
            await database.scalars(
                select(Commitment).where(
                    Commitment.user_id == user_id,
                    Commitment.workspace_id == workspace_id,
                    Commitment.status.in_(
                        ("candidate", "confirmed", "waiting", "attention")
                    ),
                )
            )
        )
        for commitment in commitments:
            base_payload = ProactiveCommitmentInput(
                commitment_id=str(commitment.id),
                user_id=str(user_id),
                workspace_id=str(workspace_id),
                timezone=timezone,
            )
            refs.append(
                await self._ensure_started(
                    database,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    entity_type="commitment",
                    entity_id=commitment.id,
                    workflow_type="commitment_lifecycle",
                    workflow_id=f"navox-commitment-lifecycle-{commitment.id}",
                    workflow_run=CommitmentLifecycleWorkflow.run,
                    payload=base_payload,
                    settings=settings,
                    execution_timeout=timedelta(days=366),
                )
            )
            if commitment.status == "waiting":
                wait_material = (
                    commitment.waiting_since.isoformat()
                    if commitment.waiting_since is not None
                    else commitment.updated_at.isoformat()
                )
                refs.append(
                    await self._ensure_started(
                        database,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        entity_type="commitment",
                        entity_id=commitment.id,
                        workflow_type="follow_up",
                        workflow_id=(
                            f"navox-follow-up-{commitment.id}-"
                            f"{state_suffix(wait_material)}"
                        ),
                        workflow_run=FollowUpWorkflow.run,
                        payload=base_payload,
                        settings=settings,
                        execution_timeout=timedelta(days=366),
                        restart_terminal=True,
                    )
                )
            if commitment.commitment_type == "meeting" and commitment.due_at is not None:
                refs.append(
                    await self._ensure_started(
                        database,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        entity_type="commitment",
                        entity_id=commitment.id,
                        workflow_type="meeting_preparation",
                        workflow_id=(
                            f"navox-meeting-prep-{commitment.id}-"
                            f"{state_suffix(commitment.due_at.isoformat())}"
                        ),
                        workflow_run=MeetingPreparationWorkflow.run,
                        payload=base_payload,
                        settings=settings,
                        execution_timeout=timedelta(days=31),
                        restart_terminal=True,
                    )
                )
        return refs
