"""Connection provenance for saved plans and their execution artifacts."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.communications import CommunicationDraft, CommunicationDraftVersion
from navox.db.models import (
    Action,
    Approval,
    AuditEvent,
    Commitment,
    CommitmentSource,
    Connection,
    Plan,
    PlanSource,
    PlanStep,
    WorkflowRef,
)


async def record_plan_sources(
    database: AsyncSession,
    plan: Plan,
    commitment_ids: set[UUID],
    *,
    explicit_connections: set[UUID] | None = None,
) -> None:
    # Caller holds the owner User lock, shared with connector deletion/ingestion.
    # Inspect all source edges, including those beyond the context display limit.
    connections = set(explicit_connections or ())
    complete = True
    for identifier in commitment_ids:
        commitment = await database.get(Commitment, identifier)
        if commitment is None or (commitment.workspace_id, commitment.user_id) != (
            plan.workspace_id,
            plan.user_id,
        ):
            complete = False
            continue
        sources = list(
            await database.scalars(
                select(CommitmentSource).where(CommitmentSource.commitment_id == identifier)
            )
        )
        if not sources and commitment.created_by != "user":
            complete = False
        for source in sources:
            if source.connection_id:
                connections.add(source.connection_id)
            elif (source.provider, source.source_type) != ("navox", "manual"):
                complete = False
    for identifier in connections:
        connection = await database.get(Connection, identifier)
        if connection is None or (connection.workspace_id, connection.user_id) != (
            plan.workspace_id,
            plan.user_id,
        ):
            complete = False
            continue
        database.add(PlanSource(plan_id=plan.id, connection_id=identifier))
    plan.source_attributed = complete


async def erase_source_plans(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, connection_ids: set[UUID]
) -> None:
    if not connection_ids:
        return
    malformed_action = await database.scalar(
        select(Action.id)
        .outerjoin(PlanStep, PlanStep.id == Action.plan_step_id)
        .outerjoin(Plan, Plan.id == PlanStep.plan_id)
        .where(
            Action.workspace_id == workspace_id,
            Action.user_id == user_id,
            (Plan.id.is_(None)) | (Plan.workspace_id != workspace_id) | (Plan.user_id != user_id),
        )
        .limit(1)
    )
    if malformed_action is not None:
        raise HTTPException(409, "Plan source provenance needs manual review")
    # A legacy snapshot can contain source text even after its commitment is gone.
    if await database.scalar(
        select(Plan.id)
        .where(
            Plan.workspace_id == workspace_id,
            Plan.user_id == user_id,
            Plan.source_attributed.is_(False),
        )
        .limit(1)
    ):
        raise HTTPException(409, "Plans or actions require a reviewed deletion")
    plans = list(
        await database.scalars(
            select(Plan).where(
                Plan.id.in_(
                    select(PlanSource.plan_id).where(PlanSource.connection_id.in_(connection_ids))
                )
            )
        )
    )
    if any((p.workspace_id, p.user_id) != (workspace_id, user_id) for p in plans):
        raise HTTPException(409, "Plan source provenance needs manual review")
    ids = {p.id for p in plans}
    if not ids:
        await erase_source_drafts(
            database, workspace_id=workspace_id, user_id=user_id, connection_ids=connection_ids
        )
        return
    foreign_source = await database.scalar(
        select(PlanSource.connection_id)
        .outerjoin(Connection, Connection.id == PlanSource.connection_id)
        .where(
            PlanSource.plan_id.in_(ids),
            Connection.id.is_(None)
            | (Connection.workspace_id != workspace_id)
            | (Connection.user_id != user_id),
        )
        .limit(1)
    )
    if foreign_source is not None:
        raise HTTPException(409, "Plan source provenance needs manual review")
    # Only quiescent histories are erasable. A committed external execution marker
    # survives the provider request; never erase it while the outcome is pending.
    terminal = {"completed", "blocked", "failed", "rejected", "expired", "manual_review"}
    if any(p.status not in terminal for p in plans):
        raise HTTPException(409, "Source plan execution must finish before deletion")
    steps = list(await database.scalars(select(PlanStep).where(PlanStep.plan_id.in_(ids))))
    actions = list(
        await database.scalars(select(Action).where(Action.plan_step_id.in_([s.id for s in steps])))
    )
    approvals = list(
        await database.scalars(
            select(Approval).where(Approval.action_id.in_([a.id for a in actions]))
        )
    )
    if any((a.workspace_id, a.user_id) != (workspace_id, user_id) for a in actions) or any(
        (a.workspace_id, a.user_id) != (workspace_id, user_id) for a in approvals
    ):
        raise HTTPException(409, "Plan source provenance needs manual review")
    uncertain = {a.id for a in actions if a.status == "uncertain"}
    if any(a.status not in terminal | {"uncertain"} for a in actions) or any(
        a.status in {"pending", "approved"}
        or (a.status == "consuming" and a.action_id not in uncertain)
        for a in approvals
    ):
        raise HTTPException(409, "Source action execution must finish before deletion")
    # Explicit ordered deletes also work with SQLite test connections without FK
    # enforcement. Other sources' commitments and independent plans are retained.
    await erase_source_drafts(
        database, workspace_id=workspace_id, user_id=user_id, connection_ids=connection_ids
    )
    await database.execute(delete(Approval).where(Approval.id.in_([a.id for a in approvals])))
    await database.execute(delete(Action).where(Action.id.in_([a.id for a in actions])))
    await database.execute(delete(PlanStep).where(PlanStep.plan_id.in_(ids)))
    await database.execute(delete(PlanSource).where(PlanSource.plan_id.in_(ids)))
    await database.execute(
        delete(WorkflowRef).where(
            WorkflowRef.workspace_id == workspace_id,
            WorkflowRef.user_id == user_id,
            ((WorkflowRef.entity_type == "plan") & WorkflowRef.entity_id.in_(ids))
            | (
                (WorkflowRef.entity_type == "action")
                & WorkflowRef.entity_id.in_([a.id for a in actions])
            ),
        )
    )
    await database.execute(
        delete(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.user_id == user_id,
            AuditEvent.entity_id.in_(
                {*ids, *(s.id for s in steps), *(a.id for a in actions), *(a.id for a in approvals)}
            ),
        )
    )
    await database.execute(delete(Plan).where(Plan.id.in_(ids)))


async def erase_source_drafts(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, connection_ids: set[UUID]
) -> None:
    drafts = list(
        await database.scalars(
            select(CommunicationDraft).where(
                CommunicationDraft.source_connection_id.in_(connection_ids)
            )
        )
    )
    if any((d.workspace_id, d.user_id) != (workspace_id, user_id) for d in drafts):
        raise HTTPException(409, "Draft source provenance needs manual review")
    for draft in drafts:
        action = await database.get(Action, draft.action_id) if draft.action_id else None
        if action is not None and (
            (action.workspace_id, action.user_id) != (workspace_id, user_id)
            or action.status
            not in {"completed", "blocked", "failed", "rejected", "expired", "uncertain"}
        ):
            raise HTTPException(409, "Draft send must finish before deleting its source")
    identifiers = [d.id for d in drafts]
    await database.execute(
        delete(CommunicationDraftVersion).where(CommunicationDraftVersion.draft_id.in_(identifiers))
    )
    await database.execute(delete(CommunicationDraft).where(CommunicationDraft.id.in_(identifiers)))
