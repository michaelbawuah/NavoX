"""NavoX owns session identity. Opaque references are not permission to resolve content."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.foundation.contracts import AIResult
from navox.db.ai_registry import AITaskRun
from navox.db.communications import AssistantSession, AssistantTurn
from navox.db.models import User, WorkspaceMembership


async def authorize(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    user = await database.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        user is None
        or user.agent_paused
        or await database.get(WorkspaceMembership, (workspace_id, user_id)) is None
    ):
        raise ValueError("Session access denied")


async def create_session(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID
) -> AssistantSession:
    await authorize(database, user_id, workspace_id)
    session = AssistantSession(
        id=uuid4(), user_id=user_id, workspace_id=workspace_id, mode="PERSONAL", status="active"
    )
    database.add(session)
    await database.flush()
    return session


async def append_turn(
    database: AsyncSession,
    *,
    session_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    result: AIResult,
    user_input_reference: str,
    context_snapshot_reference: str,
    output_reference: str,
) -> AssistantTurn:
    result = AIResult.model_validate(result)
    await authorize(database, user_id, workspace_id)
    if (result.workspace_id, result.user_id) != (workspace_id, user_id):
        raise ValueError("Turn result belongs to another account")
    session = await database.scalar(
        select(AssistantSession)
        .where(
            AssistantSession.id == session_id,
            AssistantSession.workspace_id == workspace_id,
            AssistantSession.user_id == user_id,
            AssistantSession.status == "active",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if session is None:
        raise ValueError("Session is unavailable")
    run = await database.scalar(
        select(AITaskRun).where(
            AITaskRun.task_id == result.task_id,
            AITaskRun.workspace_id == workspace_id,
            AITaskRun.user_id == user_id,
            AITaskRun.status == "COMPLETED",
            AITaskRun.shadow.is_(False),
            AITaskRun.provider == result.provider.value,
            AITaskRun.model == result.model,
            AITaskRun.trace_id == result.trace_id,
        )
    )
    if run is None or not (
        result.schema_validated and result.semantic_validated and result.policy_validated
    ):
        raise ValueError("Turn requires a successful validated task for this account")
    # A future artifact service must independently authorize dereferencing.
    # Until then store only scoped opaque IDs, never arbitrary input or model text.
    prefix = f"navox:{workspace_id}:{user_id}:"
    for reference in (user_input_reference, context_snapshot_reference, output_reference):
        if not reference.startswith(prefix):
            raise ValueError("Unscoped session artifact reference")
        UUID(reference[len(prefix) :])
    existing = await database.scalar(
        select(AssistantTurn).where(
            AssistantTurn.session_id == session_id,
            AssistantTurn.task_id == result.task_id,
        )
    )
    if existing is not None:
        if (
            existing.user_input_reference,
            existing.context_snapshot_reference,
            existing.output_reference,
        ) != (user_input_reference, context_snapshot_reference, output_reference):
            raise ValueError("Task was already appended with different references")
        return existing
    next_sequence = await database.scalar(
        update(AssistantSession)
        .where(
            AssistantSession.id == session_id,
            AssistantSession.next_sequence == session.next_sequence,
        )
        .values(next_sequence=AssistantSession.next_sequence + 1, updated_at=datetime.now(UTC))
        .returning(AssistantSession.next_sequence)
    )
    if next_sequence is None:
        raise ValueError("Session changed; retry this turn")
    turn = AssistantTurn(
        session_id=session_id,
        sequence=next_sequence - 1,
        task_id=result.task_id,
        user_input_reference=user_input_reference,
        context_snapshot_reference=context_snapshot_reference,
        output_reference=output_reference,
        provider=result.provider.value,
        model=result.model,
        action_refs=[],
    )
    database.add(turn)
    await database.flush()
    return turn
