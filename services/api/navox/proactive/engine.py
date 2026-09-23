import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.db.models import (
    BriefingSnapshot,
    Commitment,
    CommitmentRelation,
    Objective,
    ProactivePreference,
    ProactiveSignal,
    Workspace,
)
from navox.intelligence.attention import workspace_attention

ACTIVE_COMMITMENT_STATUSES = {"candidate", "confirmed", "waiting", "attention"}
TERMINAL_COMMITMENT_STATUSES = {"completed", "rejected", "expired"}
CONSEQUENCE = {
    "deadline": 18,
    "meeting": 14,
    "follow_up": 12,
    "promise": 16,
    "renewal": 14,
    "task": 8,
}
SIGNAL_TYPES = {
    "deadline": "deadline_warning",
    "meeting": "meeting_prep",
    "follow_up": "follow_up_due",
    "promise": "promise_follow_up",
    "renewal": "renewal_warning",
    "task": "task_attention",
}


class InvalidProactiveTimezone(ValueError):
    pass


@dataclass(frozen=True)
class ScoreBreakdown:
    urgency: int
    consequence: int
    user_priority: int
    objective_relevance: int
    actionability: int
    waiting_duration: int
    interruption_cost: int
    notification_fatigue: int

    @property
    def score(self) -> int:
        positive = (
            self.urgency
            + self.consequence
            + self.user_priority
            + self.objective_relevance
            + self.actionability
            + self.waiting_duration
        )
        negative = self.interruption_cost + self.notification_fatigue
        return max(0, min(100, positive - negative))

    def as_dict(self) -> dict[str, int]:
        return {
            "urgency": self.urgency,
            "consequence": self.consequence,
            "user_priority": self.user_priority,
            "objective_relevance": self.objective_relevance,
            "actionability": self.actionability,
            "waiting_duration": self.waiting_duration,
            "interruption_cost": self.interruption_cost,
            "notification_fatigue": self.notification_fatigue,
        }


@dataclass(frozen=True)
class EvaluatedSignal:
    signal_type: str
    score: int
    components: ScoreBreakdown
    tier: str
    what_happening: str
    why_matters: str
    suggested_capability: str | None
    fingerprint: str


@dataclass(frozen=True)
class MeetingPrep:
    commitment_id: UUID
    title: str
    starts_at: datetime
    minutes_until: int
    description: str | None
    related_commitments: tuple[tuple[UUID, str, str], ...]
    prep_points: tuple[str, ...]


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def timezone_for(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise InvalidProactiveTimezone(f"Unknown IANA timezone: {name}") from error


def active_commitment(commitment: Commitment, now: datetime) -> bool:
    if commitment.status not in ACTIVE_COMMITMENT_STATUSES:
        return False
    if commitment.valid_from is not None and aware(commitment.valid_from) > now:
        return False
    if commitment.valid_until is not None and aware(commitment.valid_until) < now:
        return False
    return True


def urgency_component(commitment: Commitment, now: datetime) -> int:
    if commitment.due_at is None:
        return 0
    seconds = (aware(commitment.due_at) - now).total_seconds()
    if seconds < 0:
        return 35
    if seconds <= 2 * 60 * 60:
        return 34 if commitment.commitment_type == "meeting" else 32
    if seconds <= 24 * 60 * 60:
        return 28
    if seconds <= 3 * 24 * 60 * 60:
        return 22
    if seconds <= 7 * 24 * 60 * 60:
        return 14
    if seconds <= 30 * 24 * 60 * 60:
        return 6
    return 0


def waiting_component(commitment: Commitment, now: datetime) -> int:
    if commitment.status != "waiting":
        return 0
    since = aware(commitment.waiting_since or commitment.updated_at or commitment.created_at)
    age = now - since
    if age >= timedelta(days=14):
        return 18
    if age >= timedelta(days=7):
        return 14
    if age >= timedelta(days=3):
        return 10
    if age >= timedelta(days=1):
        return 5
    return 0


def actionability_component(commitment: Commitment) -> int:
    if commitment.status == "candidate":
        return 6
    if commitment.status == "attention":
        return 12
    if commitment.status == "waiting":
        return 7
    return 10


def signal_type_for(commitment: Commitment) -> str:
    if commitment.status == "candidate":
        return "candidate_review"
    if commitment.status == "waiting":
        return "waiting_response"
    return SIGNAL_TYPES.get(commitment.commitment_type, "task_attention")


def capability_for(signal_type: str) -> str | None:
    if signal_type == "candidate_review":
        return "commitment.review"
    if signal_type == "meeting_prep":
        return "meeting.prepare"
    if signal_type in {
        "deadline_warning",
        "renewal_warning",
        "promise_follow_up",
        "follow_up_due",
        "waiting_response",
        "task_attention",
    }:
        return "commitment.handle"
    return None


def fingerprint_for(commitment: Commitment, signal_type: str) -> str:
    material = {
        "commitment_id": str(commitment.id),
        "signal_type": signal_type,
        "type": commitment.commitment_type,
        "due_at": aware(commitment.due_at).isoformat() if commitment.due_at else None,
        "priority": commitment.priority,
        "status": commitment.status,
    }
    return sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def what_happening(commitment: Commitment, signal_type: str) -> str:
    labels = {
        "candidate_review": "NavoX found a commitment that still needs your review.",
        "meeting_prep": "An upcoming meeting is on your operational timeline.",
        "deadline_warning": "A saved deadline is approaching or overdue.",
        "renewal_warning": "A saved renewal is approaching.",
        "promise_follow_up": "A promise you made is still active.",
        "follow_up_due": "A follow-up is approaching or due.",
        "waiting_response": "You are still waiting on an external response or dependency.",
        "task_attention": "A saved task has become important enough to surface.",
    }
    return labels.get(signal_type, "A saved commitment needs attention")


def why_matters(commitment: Commitment, signal_type: str, now: datetime) -> str:
    if commitment.due_at is not None:
        due = aware(commitment.due_at)
        if due < now:
            return f"{commitment.title} is overdue and remains unresolved."
        delta = due - now
        hours = max(0, round(delta.total_seconds() / 3600))
        if hours < 24:
            return f"{commitment.title} is due in about {hours} hours."
        days = max(1, round(delta.total_seconds() / 86400))
        return f"{commitment.title} is due in about {days} days."
    if signal_type == "waiting_response":
        return f"{commitment.title} is blocked on someone or something outside your control."
    if signal_type == "candidate_review":
        return (
            f"{commitment.title} is not confirmed yet, so NavoX will not treat it as settled fact."
        )
    return f"{commitment.title} is priority {commitment.priority} and remains active."


def parse_clock(value: str) -> time:
    hour_text, minute_text = value.split(":", maxsplit=1)
    return time(hour=int(hour_text), minute=int(minute_text))


def in_quiet_hours(local_now: datetime, preference: ProactivePreference) -> bool:
    start = parse_clock(preference.quiet_hours_start)
    end = parse_clock(preference.quiet_hours_end)
    current = local_now.timetz().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


async def default_preference(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
) -> ProactivePreference:
    # API requests and several lifecycle activities evaluate the same workspace.
    # Hold one workspace lock until the caller commits, before reading either
    # preferences or signals, so concurrent evaluators cannot insert duplicates.
    # NO KEY UPDATE still permits unrelated inserts referencing this workspace.
    await database.scalar(
        select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(key_share=True)
    )
    existing = await database.scalar(
        select(ProactivePreference)
        .where(
            ProactivePreference.user_id == user_id,
            ProactivePreference.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        return existing
    preference = ProactivePreference(user_id=user_id, workspace_id=workspace_id)
    database.add(preference)
    await database.flush()
    return preference


async def interruption_count_for_day(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    timezone: ZoneInfo,
    now: datetime,
) -> int:
    local_now = now.astimezone(timezone)
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=timezone)
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(UTC)
    end_utc = local_end.astimezone(UTC)
    signals = list(
        await database.scalars(
            select(ProactiveSignal).where(
                ProactiveSignal.user_id == user_id,
                ProactiveSignal.workspace_id == workspace_id,
                ProactiveSignal.tier == "notify_now",
                ProactiveSignal.last_surfaced_at.is_not(None),
                ProactiveSignal.last_surfaced_at >= start_utc,
                ProactiveSignal.last_surfaced_at < end_utc,
            )
        )
    )
    return len(signals)


def fatigue_penalty(
    signal: ProactiveSignal | None,
    *,
    now: datetime,
    cooldown_minutes: int,
) -> int:
    if signal is None or signal.last_surfaced_at is None:
        return 0
    since = now - aware(signal.last_surfaced_at)
    cooldown = timedelta(minutes=cooldown_minutes)
    if since < cooldown / 4:
        return 24
    if since < cooldown:
        return 14
    if signal.surface_count >= 3 and since < timedelta(days=1):
        return 8
    return 0


def base_score(
    commitment: Commitment,
    *,
    now: datetime,
    objective_active: bool,
    notification_fatigue: int,
) -> ScoreBreakdown:
    return ScoreBreakdown(
        urgency=urgency_component(commitment, now),
        consequence=CONSEQUENCE.get(commitment.commitment_type, 8),
        user_priority=max(4, min(20, commitment.priority * 4)),
        objective_relevance=8 if objective_active else 0,
        actionability=actionability_component(commitment),
        waiting_duration=waiting_component(commitment, now),
        interruption_cost=0,
        notification_fatigue=notification_fatigue,
    )


def tier_for(
    score: int,
    preference: ProactivePreference,
    *,
    quiet: bool,
    interruption_budget_exhausted: bool,
    notifications_allowed: bool,
) -> str:
    if score < preference.dashboard_threshold:
        return "suppressed"
    if score < preference.briefing_threshold:
        return "dashboard"
    if score < preference.notify_threshold:
        return "briefing"
    if not notifications_allowed or quiet or interruption_budget_exhausted:
        return "briefing"
    return "notify_now"


def should_have_signal(commitment: Commitment, now: datetime) -> bool:
    if commitment.intelligence_metadata:
        return True
    if commitment.status in {"candidate", "waiting", "attention"}:
        return True
    if commitment.due_at is not None:
        return aware(commitment.due_at) <= now + timedelta(days=30)
    return commitment.priority >= 4


async def evaluate_workspace(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    timezone_name: str,
    now: datetime | None = None,
) -> list[ProactiveSignal]:
    timezone = timezone_for(timezone_name)
    current_time = aware(now or datetime.now(UTC))
    local_now = current_time.astimezone(timezone)
    preference = await default_preference(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    interruptions = await interruption_count_for_day(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        timezone=timezone,
        now=current_time,
    )
    budget_exhausted = interruptions >= preference.max_interruptions_per_day

    commitments, intelligence_scores = await workspace_attention(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        now=current_time,
    )
    existing_signals = list(
        await database.scalars(
            select(ProactiveSignal)
            .where(
                ProactiveSignal.user_id == user_id,
                ProactiveSignal.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        )
    )
    signal_by_fingerprint = {signal.fingerprint: signal for signal in existing_signals}

    objective_ids = {c.objective_id for c in commitments if c.objective_id is not None}
    active_objectives: set[UUID] = set()
    if objective_ids:
        objectives = list(
            await database.scalars(
                select(Objective).where(
                    Objective.id.in_(objective_ids),
                    Objective.user_id == user_id,
                    Objective.workspace_id == workspace_id,
                    Objective.status == "active",
                )
            )
        )
        active_objectives = {objective.id for objective in objectives}

    evaluated: list[ProactiveSignal] = []
    current_fingerprints: set[str] = set()

    for commitment in commitments:
        if not active_commitment(commitment, current_time):
            continue
        if not should_have_signal(commitment, current_time):
            continue
        signal_type = signal_type_for(commitment)
        fingerprint = fingerprint_for(commitment, signal_type)
        current_fingerprints.add(fingerprint)
        signal = signal_by_fingerprint.get(fingerprint)
        intelligence = bool(commitment.intelligence_metadata)
        if intelligence and signal is None:
            # A source correction retains the same alert and its dismissal/cooldown
            # history instead of manufacturing a fresh notification identity.
            signal = next(
                (
                    item
                    for item in existing_signals
                    if item.commitment_id == commitment.id and item.status != "resolved"
                ),
                None,
            )
            if signal is not None:
                signal.fingerprint = fingerprint
                signal.signal_type = signal_type
        fatigue = fatigue_penalty(
            signal,
            now=current_time,
            cooldown_minutes=preference.cooldown_minutes,
        )
        components = base_score(
            commitment,
            now=current_time,
            objective_active=commitment.objective_id in active_objectives,
            notification_fatigue=fatigue,
        )
        score = components.score
        quiet = in_quiet_hours(local_now, preference)

        snoozed = (
            signal is not None
            and signal.snoozed_until is not None
            and aware(signal.snoozed_until) > current_time
        )
        dismissed = signal is not None and signal.status == "dismissed"
        if (
            signal is not None
            and signal.snoozed_until is not None
            and aware(signal.snoozed_until) <= current_time
        ):
            signal.snoozed_until = None
        tier = (
            "suppressed"
            if snoozed or dismissed
            else tier_for(
                score,
                preference,
                quiet=quiet,
                interruption_budget_exhausted=budget_exhausted,
                notifications_allowed=preference.notifications_enabled,
            )
        )

        components_data = {name: float(value) for name, value in components.as_dict().items()}
        explanation = why_matters(commitment, signal_type, current_time)
        capability = capability_for(signal_type)
        if intelligence:
            result = intelligence_scores[commitment.id]
            score = result.score
            components_data = dict(result.factors)
            explanation = "; ".join(result.reasons)
            capability = result.suggested_capability
            band_tiers = {
                "NOW": "notify_now",
                "TODAY_HIGH": "briefing",
                "TODAY": "briefing",
                "DASHBOARD": "dashboard",
                "SUPPRESS": "suppressed",
            }
            tier = band_tiers[result.band]
            allowed = tier_for(
                score,
                preference,
                quiet=quiet,
                interruption_budget_exhausted=budget_exhausted,
                notifications_allowed=preference.notifications_enabled,
            )
            order = {"suppressed": 0, "dashboard": 1, "briefing": 2, "notify_now": 3}
            if order[allowed] < order[tier]:
                tier = allowed
            if commitment.status == "candidate" and order[tier] > 1:
                tier = "dashboard"
            if result.suppressed or snoozed or dismissed:
                tier = "suppressed"
            commitment.attention_score = score
            commitment.attention_factors = dict(result.factors)
            commitment.attention_band = result.band

        if signal is None:
            signal = ProactiveSignal(
                user_id=user_id,
                workspace_id=workspace_id,
                commitment_id=commitment.id,
                signal_type=signal_type,
                fingerprint=fingerprint,
                status="active",
                tier=tier,
                attention_score=score,
                score_components=components_data,
                what_happening=what_happening(commitment, signal_type),
                why_matters=explanation,
                suggested_capability=capability,
                last_evaluated_at=current_time,
            )
            database.add(signal)
            await database.flush()
            add_audit_event(
                database,
                user_id=user_id,
                workspace_id=workspace_id,
                event_type="proactive.signal.created",
                entity_type="proactive_signal",
                entity_id=signal.id,
                metadata={
                    "signal_type": signal_type,
                    "attention_score": score,
                    "tier": tier,
                    "commitment_id": str(commitment.id),
                },
            )
        else:
            signal.tier = tier
            signal.attention_score = score
            signal.score_components = components_data
            signal.what_happening = what_happening(commitment, signal_type)
            signal.why_matters = explanation
            signal.suggested_capability = capability
            signal.last_evaluated_at = current_time
            if signal.status == "resolved":
                signal.status = "active"
                signal.resolved_at = None
        evaluated.append(signal)

    for signal in existing_signals:
        if signal.commitment_id is None:
            continue
        if signal.fingerprint in current_fingerprints:
            continue
        if signal.status != "resolved":
            signal.status = "resolved"
            signal.tier = "suppressed"
            signal.resolved_at = current_time
            add_audit_event(
                database,
                user_id=user_id,
                workspace_id=workspace_id,
                event_type="proactive.signal.resolved",
                entity_type="proactive_signal",
                entity_id=signal.id,
                metadata={"commitment_id": str(signal.commitment_id)},
            )

    await database.commit()
    return sorted(evaluated, key=lambda item: (-item.attention_score, item.created_at))


async def visible_signals(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
) -> list[ProactiveSignal]:
    return list(
        await database.scalars(
            select(ProactiveSignal)
            .where(
                ProactiveSignal.user_id == user_id,
                ProactiveSignal.workspace_id == workspace_id,
                ProactiveSignal.status == "active",
                ProactiveSignal.tier != "suppressed",
            )
            .order_by(ProactiveSignal.attention_score.desc(), ProactiveSignal.created_at)
        )
    )


def briefing_hash(signals: list[ProactiveSignal]) -> str:
    payload = [
        {
            "id": str(signal.id),
            "score": signal.attention_score,
            "tier": signal.tier,
            "why": signal.why_matters,
        }
        for signal in signals
    ]
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def record_briefing(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    timezone_name: str,
    request_id: UUID,
    signals: list[ProactiveSignal],
    now: datetime | None = None,
) -> BriefingSnapshot:
    current_time = aware(now or datetime.now(UTC))
    timezone = timezone_for(timezone_name)
    existing = await database.scalar(
        select(BriefingSnapshot).where(
            BriefingSnapshot.workspace_id == workspace_id,
            BriefingSnapshot.request_id == request_id,
        )
    )
    if existing is not None:
        return existing
    snapshot = BriefingSnapshot(
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=request_id,
        local_date=current_time.astimezone(timezone).date().isoformat(),
        timezone=timezone_name,
        content_hash=briefing_hash(signals),
        signal_ids=[str(signal.id) for signal in signals],
        item_count=len(signals),
        generated_at=current_time,
    )
    database.add(snapshot)
    await database.flush()
    add_audit_event(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        event_type="proactive.briefing.generated",
        entity_type="briefing_snapshot",
        entity_id=snapshot.id,
        metadata={
            "item_count": len(signals),
            "content_hash": snapshot.content_hash,
            "timezone": timezone_name,
        },
    )
    await database.commit()
    return snapshot


async def mark_signals_surfaced(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    signals: list[ProactiveSignal],
    surface: str,
    now: datetime | None = None,
) -> None:
    current_time = aware(now or datetime.now(UTC))
    for signal in signals:
        signal.last_surfaced_at = current_time
        signal.surface_count += 1
        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="proactive.signal.surfaced",
            entity_type="proactive_signal",
            entity_id=signal.id,
            metadata={
                "surface": surface,
                "attention_score": signal.attention_score,
                "tier": signal.tier,
            },
        )
    await database.commit()


async def next_meeting_prep(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    now: datetime | None = None,
) -> MeetingPrep | None:
    current_time = aware(now or datetime.now(UTC))
    meetings = list(
        await database.scalars(
            select(Commitment)
            .where(
                Commitment.user_id == user_id,
                Commitment.workspace_id == workspace_id,
                Commitment.commitment_type == "meeting",
                Commitment.status.in_(ACTIVE_COMMITMENT_STATUSES),
                Commitment.due_at.is_not(None),
            )
            .order_by(Commitment.due_at)
        )
    )
    meeting = next(
        (
            item
            for item in meetings
            if item.due_at is not None
            and aware(item.due_at) >= current_time
            and active_commitment(item, current_time)
        ),
        None,
    )
    if meeting is None or meeting.due_at is None:
        return None

    relations = list(
        await database.scalars(
            select(CommitmentRelation).where(CommitmentRelation.from_commitment_id == meeting.id)
        )
    )
    related_ids = {relation.to_commitment_id for relation in relations}
    related: list[Commitment] = []
    if related_ids:
        related = list(
            await database.scalars(
                select(Commitment).where(
                    Commitment.id.in_(related_ids),
                    Commitment.user_id == user_id,
                    Commitment.workspace_id == workspace_id,
                )
            )
        )
    related_rows = tuple(
        (item.id, item.title, item.status)
        for item in sorted(related, key=lambda value: value.title.casefold())
    )
    minutes_until = max(
        0,
        round((aware(meeting.due_at) - current_time).total_seconds() / 60),
    )
    points = [
        f"Meeting starts in about {minutes_until} minutes.",
        "Review the saved meeting description and any related commitments.",
    ]
    if meeting.description:
        points.append(meeting.description)
    if related_rows:
        points.append(f"{len(related_rows)} related commitment(s) are linked to this meeting.")
    else:
        points.append("No related commitments are currently linked.")
    return MeetingPrep(
        commitment_id=meeting.id,
        title=meeting.title,
        starts_at=aware(meeting.due_at),
        minutes_until=minutes_until,
        description=meeting.description,
        related_commitments=related_rows,
        prep_points=tuple(points),
    )
