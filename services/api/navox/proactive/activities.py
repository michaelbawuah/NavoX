from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from temporalio import activity

from navox.agent.audit import add_audit_event
from navox.db.models import Commitment, ProactivePreference, User, WorkflowRef
from navox.db.session import get_session_factory
from navox.proactive.engine import (
    active_commitment,
    default_preference,
    evaluate_workspace,
    mark_signals_surfaced,
    next_meeting_prep,
    record_briefing,
    visible_signals,
)


@dataclass(frozen=True)
class ProactiveWorkspaceInput:
    user_id: str
    workspace_id: str
    timezone: str


@dataclass(frozen=True)
class ProactiveCommitmentInput:
    commitment_id: str
    user_id: str
    workspace_id: str
    timezone: str


@dataclass(frozen=True)
class CommitmentTimingState:
    state: str
    status: str | None
    commitment_type: str | None
    due_at: str | None


@dataclass(frozen=True)
class WorkflowStatusInput:
    entity_type: str
    entity_id: str
    workflow_type: str
    status: str


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@activity.defn
async def evaluate_proactive_workspace_activity(payload: ProactiveWorkspaceInput) -> int:
    async with get_session_factory()() as database:
        signals = await evaluate_workspace(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
            timezone_name=payload.timezone,
        )
        return len(signals)


@activity.defn
async def commitment_timing_state_activity(
    payload: ProactiveCommitmentInput,
) -> CommitmentTimingState:
    async with get_session_factory()() as database:
        commitment = await database.scalar(
            select(Commitment).where(
                Commitment.id == UUID(payload.commitment_id),
                Commitment.user_id == UUID(payload.user_id),
                Commitment.workspace_id == UUID(payload.workspace_id),
            )
        )
        if commitment is None:
            return CommitmentTimingState(
                state="missing",
                status=None,
                commitment_type=None,
                due_at=None,
            )
        now = datetime.now(UTC)
        state = "active" if active_commitment(commitment, now) else "terminal"
        return CommitmentTimingState(
            state=state,
            status=commitment.status,
            commitment_type=commitment.commitment_type,
            due_at=aware(commitment.due_at).isoformat() if commitment.due_at else None,
        )


@activity.defn
async def mark_proactive_workflow_status_activity(payload: WorkflowStatusInput) -> str:
    async with get_session_factory()() as database:
        workflow_ref = await database.scalar(
            select(WorkflowRef).where(
                WorkflowRef.entity_type == payload.entity_type,
                WorkflowRef.entity_id == UUID(payload.entity_id),
                WorkflowRef.workflow_type == payload.workflow_type,
            )
        )
        if workflow_ref is None:
            return "missing"
        workflow_ref.status = payload.status
        await database.commit()
        return payload.status


@activity.defn
async def prepare_meeting_activity(payload: ProactiveCommitmentInput) -> str:
    async with get_session_factory()() as database:
        prep = await next_meeting_prep(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
        )
        if prep is None or prep.commitment_id != UUID(payload.commitment_id):
            return "no_longer_next_meeting"
        await evaluate_workspace(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
            timezone_name=payload.timezone,
        )
        add_audit_event(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
            event_type="proactive.meeting.prepared",
            entity_type="commitment",
            entity_id=prep.commitment_id,
            metadata={
                "starts_at": prep.starts_at.isoformat(),
                "minutes_until": prep.minutes_until,
                "prep_point_count": len(prep.prep_points),
            },
        )
        await database.commit()
        return "prepared"


def next_briefing_delay(
    *,
    timezone_name: str,
    briefing_hour: int,
    now: datetime,
) -> int:
    timezone = ZoneInfo(timezone_name)
    local_now = aware(now).astimezone(timezone)
    target = datetime.combine(
        local_now.date(),
        time(hour=briefing_hour),
        tzinfo=timezone,
    )
    if target <= local_now:
        target += timedelta(days=1)
    return max(1, round((target.astimezone(UTC) - aware(now)).total_seconds()))


@activity.defn
async def daily_briefing_delay_activity(payload: ProactiveWorkspaceInput) -> int:
    async with get_session_factory()() as database:
        preference = await default_preference(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
        )
        await database.commit()
        return next_briefing_delay(
            timezone_name=payload.timezone,
            briefing_hour=preference.daily_briefing_hour,
            now=datetime.now(UTC),
        )


@activity.defn
async def record_scheduled_briefing_activity(payload: ProactiveWorkspaceInput) -> str:
    async with get_session_factory()() as database:
        user = await database.scalar(select(User).where(User.id == UUID(payload.user_id)))
        if user is None:
            return "missing_user"
        preference = await database.scalar(
            select(ProactivePreference).where(
                ProactivePreference.user_id == UUID(payload.user_id),
                ProactivePreference.workspace_id == UUID(payload.workspace_id),
            )
        )
        if preference is None:
            preference = await default_preference(
                database,
                user_id=UUID(payload.user_id),
                workspace_id=UUID(payload.workspace_id),
            )

        await evaluate_workspace(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
            timezone_name=payload.timezone,
        )
        signals = await visible_signals(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
        )
        now = datetime.now(UTC)
        local_date = now.astimezone(ZoneInfo(payload.timezone)).date().isoformat()
        request_id = uuid5(
            NAMESPACE_URL,
            f"navox:scheduled-briefing:{payload.workspace_id}:{local_date}",
        )
        snapshot = await record_briefing(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
            timezone_name=payload.timezone,
            request_id=request_id,
            signals=signals,
            now=now,
        )

        interruptive = [signal for signal in signals if signal.tier == "notify_now"]
        if not user.agent_paused and preference.notifications_enabled and interruptive:
            await mark_signals_surfaced(
                database,
                user_id=UUID(payload.user_id),
                workspace_id=UUID(payload.workspace_id),
                signals=interruptive,
                surface="notify_now",
                now=now,
            )
        add_audit_event(
            database,
            user_id=UUID(payload.user_id),
            workspace_id=UUID(payload.workspace_id),
            event_type="proactive.scheduled_briefing.ready",
            entity_type="briefing_snapshot",
            entity_id=snapshot.id,
            metadata={
                "item_count": snapshot.item_count,
                "delivery_suppressed": user.agent_paused or not preference.notifications_enabled,
            },
        )
        await database.commit()
        return "paused" if user.agent_paused else "ready"
