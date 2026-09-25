"""Identifier-only durable lifecycle activities; all I/O stays outside workflows."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from temporalio import activity

from navox.core.settings import get_settings
from navox.db.models import (
    CancellationAttempt,
    RecurringObligation,
    SubscriptionEvent,
    User,
    WorkspaceMembership,
)
from navox.db.session import get_session_factory
from navox.subscriptions.events import aware, project_pending_events, reevaluate_obligation

VERIFICATION_DELAYS = (60, 900, 3600)
VERIFICATION_STATUSES = frozenset(
    {"IN_PROGRESS", "AWAITING_USER", "SUBMITTED", "VERIFICATION_PENDING"}
)


@dataclass(frozen=True)
class SubscriptionWork:
    workspace_id: str
    user_id: str
    obligation_id: str
    attempt_id: str | None = None
    revision: int | None = None


@dataclass(frozen=True)
class SubscriptionState:
    status: str
    revision: int = 0
    delay_seconds: int = 0


async def _owned_obligation(payload: SubscriptionWork) -> SubscriptionState:
    async with get_session_factory()() as database:
        user = await database.scalar(
            select(User).where(User.id == UUID(payload.user_id)).with_for_update()
        )
        membership = await database.get(
            WorkspaceMembership, (UUID(payload.workspace_id), UUID(payload.user_id))
        )
        if user is None or membership is None or user.agent_paused:
            return SubscriptionState("unavailable")
        obligation = await database.scalar(
            select(RecurringObligation)
            .where(
                RecurringObligation.id == UUID(payload.obligation_id),
                RecurringObligation.workspace_id == UUID(payload.workspace_id),
                RecurringObligation.user_id == UUID(payload.user_id),
            )
            .with_for_update()
        )
        if obligation is None:
            return SubscriptionState("missing")
        if payload.revision is not None and payload.revision != obligation.revision:
            return SubscriptionState("superseded", obligation.revision)
        now = datetime.now(UTC)
        obligation.obligation_metadata = {
            **(obligation.obligation_metadata or {}),
            "lifecycle_checked_at": now.isoformat(),
        }
        await reevaluate_obligation(database, obligation=obligation, now=now)
        await project_pending_events(
            database, workspace_id=obligation.workspace_id, user_id=user.id, now=now
        )
        await database.commit()
        if obligation.status in {"CANCELLED", "EXPIRED"} or obligation.review_state == "NOT_MINE":
            return SubscriptionState("terminal", obligation.revision)
        boundaries = [
            aware(value) - timedelta(days=7)
            for value in (obligation.next_renewal_at, obligation.trial_ends_at)
            if value is not None and aware(value) - timedelta(days=7) > now
        ]
        delay = (
            max(1, min(3600, int((min(boundaries) - now).total_seconds()))) if boundaries else 3600
        )
        return SubscriptionState("active", obligation.revision, delay)


@activity.defn
async def reevaluate_subscription_activity(payload: SubscriptionWork) -> SubscriptionState:
    return await _owned_obligation(payload)


@activity.defn
async def cancellation_state_activity(payload: SubscriptionWork) -> SubscriptionState:
    if payload.attempt_id is None:
        return SubscriptionState("missing")
    async with get_session_factory()() as database:
        user = await database.get(User, UUID(payload.user_id))
        membership = await database.get(
            WorkspaceMembership, (UUID(payload.workspace_id), UUID(payload.user_id))
        )
        if user is None or membership is None or user.agent_paused:
            return SubscriptionState("unavailable")
        attempt = await database.scalar(
            select(CancellationAttempt).where(
                CancellationAttempt.id == UUID(payload.attempt_id),
                CancellationAttempt.obligation_id == UUID(payload.obligation_id),
                CancellationAttempt.workspace_id == UUID(payload.workspace_id),
                CancellationAttempt.user_id == UUID(payload.user_id),
            )
        )
        if attempt is None:
            return SubscriptionState("missing")
        now = datetime.now(UTC)
        if attempt.status in {"FAILED", "ABORTED", "VERIFIED_CANCELLED"}:
            return SubscriptionState(attempt.status)
        if attempt.confirmed_at is None:
            if attempt.expires_at is not None and aware(attempt.expires_at) <= now:
                return SubscriptionState("expired")
            return SubscriptionState("awaiting_confirmation", delay_seconds=30)
        if attempt.status in VERIFICATION_STATUSES:
            metadata = attempt.attempt_metadata or {}
            checks = metadata.get("verification_rechecks", 0)
            if isinstance(checks, int) and checks >= len(VERIFICATION_DELAYS) + 1:
                return SubscriptionState("review_required")
            raw = metadata.get("verification_next_at")
            delay = 0
            if isinstance(raw, str):
                try:
                    delay = max(0, int((aware(datetime.fromisoformat(raw)) - now).total_seconds()))
                except ValueError:
                    delay = 0
            return SubscriptionState("verification_pending", delay_seconds=delay)
        return SubscriptionState("confirmed")


@activity.defn
async def execute_cancellation_activity(payload: SubscriptionWork) -> SubscriptionState:
    from navox.connectors.subscription_cancellation import resolve_subscription_cancellation
    from navox.subscriptions.cancellation import (
        execute_cancellation,
        get_cancellation,
        target_for_obligation,
    )

    if payload.attempt_id is None:
        return SubscriptionState("missing")
    settings = get_settings()
    async with get_session_factory()() as database:
        attempt = await get_cancellation(
            database,
            workspace_id=UUID(payload.workspace_id),
            user_id=UUID(payload.user_id),
            attempt_id=UUID(payload.attempt_id),
        )
        if attempt.obligation_id != UUID(payload.obligation_id):
            raise PermissionError("Cancellation workflow targets another obligation")
        target = await target_for_obligation(
            database,
            workspace_id=attempt.workspace_id,
            user_id=attempt.user_id,
            obligation_id=attempt.obligation_id,
        )
        capability = await resolve_subscription_cancellation(
            database, settings=settings, target=target
        )
        if capability is None:
            # Leave the unused approval intact; expiry and a new explicit review
            # are required before a restored provider can submit anything.
            return SubscriptionState("provider_unavailable")
        attempt = await execute_cancellation(database, attempt_id=attempt.id, capability=capability)
        await project_pending_events(
            database, workspace_id=attempt.workspace_id, user_id=attempt.user_id
        )
        await database.commit()
        return SubscriptionState(attempt.status)


@activity.defn
async def verify_cancellation_activity(payload: SubscriptionWork) -> SubscriptionState:
    from navox.connectors.subscription_cancellation import resolve_subscription_cancellation
    from navox.subscriptions.cancellation import (
        get_cancellation,
        verify_cancellation,
    )
    from navox.subscriptions.cancellation_contracts import CancellationPreview

    if payload.attempt_id is None:
        return SubscriptionState("missing")
    settings = get_settings()
    async with get_session_factory()() as database:
        attempt = await get_cancellation(
            database,
            workspace_id=UUID(payload.workspace_id),
            user_id=UUID(payload.user_id),
            attempt_id=UUID(payload.attempt_id),
        )
        if attempt.obligation_id != UUID(payload.obligation_id):
            raise PermissionError("Cancellation workflow targets another obligation")
        metadata = dict(attempt.attempt_metadata or {})
        raw_count = metadata.get("verification_rechecks", 0)
        checks = raw_count if isinstance(raw_count, int) else 0
        if checks >= len(VERIFICATION_DELAYS) + 1 or attempt.status not in VERIFICATION_STATUSES:
            return SubscriptionState(attempt.status)
        now = datetime.now(UTC)
        raw_next = metadata.get("verification_next_at")
        if isinstance(raw_next, str):
            try:
                delay = (aware(datetime.fromisoformat(raw_next)) - now).total_seconds()
            except ValueError:
                delay = 0
            if delay > 0:
                return SubscriptionState("verification_pending", delay_seconds=max(1, int(delay)))
        # Persist a bounded read-attempt slot before I/O. Worker loss cannot reset
        # retry budgets; independent reconciliation honors the same durable deadline.
        metadata["verification_rechecks"] = checks + 1
        metadata["verification_next_at"] = (
            now + timedelta(seconds=VERIFICATION_DELAYS[min(checks, len(VERIFICATION_DELAYS) - 1)])
        ).isoformat()
        metadata["verification_exhausted"] = checks >= len(VERIFICATION_DELAYS)
        attempt.attempt_metadata = metadata
        await database.commit()
        # Verification addresses the dispatched provider target even when a later
        # receipt has changed the registry revision or its inferred billing facts.
        target = CancellationPreview.model_validate(attempt.preview).target
        capability = await resolve_subscription_cancellation(
            database, settings=settings, target=target
        )
        if capability is None:
            return SubscriptionState("provider_unavailable")
        attempt = await verify_cancellation(database, attempt_id=attempt.id, capability=capability)
        await project_pending_events(
            database, workspace_id=attempt.workspace_id, user_id=attempt.user_id
        )
        await database.commit()
        return SubscriptionState(attempt.status)


@activity.defn
async def pending_subscriptions_activity() -> list[SubscriptionWork]:
    """Recover committed outbox / dispatch gaps without requiring an AI provider."""
    async with get_session_factory()() as database:
        obligations = list(
            await database.scalars(
                select(RecurringObligation)
                .join(User, User.id == RecurringObligation.user_id)
                .join(
                    WorkspaceMembership,
                    (WorkspaceMembership.user_id == RecurringObligation.user_id)
                    & (WorkspaceMembership.workspace_id == RecurringObligation.workspace_id),
                )
                .where(
                    User.agent_paused.is_(False),
                    or_(
                        RecurringObligation.status.not_in(("CANCELLED", "EXPIRED")),
                        RecurringObligation.id.in_(
                            select(SubscriptionEvent.obligation_id).where(
                                SubscriptionEvent.delivery_state == "PENDING"
                            )
                        ),
                    ),
                )
                .order_by(RecurringObligation.updated_at, RecurringObligation.id)
                .limit(100)
            )
        )
        pending = [
            SubscriptionWork(
                str(row.workspace_id), str(row.user_id), str(row.id), revision=row.revision
            )
            for row in obligations
        ]
        attempts = list(
            await database.scalars(
                select(CancellationAttempt)
                .join(User, User.id == CancellationAttempt.user_id)
                .join(
                    WorkspaceMembership,
                    (WorkspaceMembership.user_id == CancellationAttempt.user_id)
                    & (WorkspaceMembership.workspace_id == CancellationAttempt.workspace_id),
                )
                .where(
                    User.agent_paused.is_(False),
                    CancellationAttempt.confirmed_at.is_not(None),
                    CancellationAttempt.status.not_in(("FAILED", "ABORTED", "VERIFIED_CANCELLED")),
                )
                .order_by(CancellationAttempt.requested_at, CancellationAttempt.id)
            )
        )
        for attempt in attempts:
            checks = (attempt.attempt_metadata or {}).get("verification_rechecks", 0)
            if isinstance(checks, int) and checks >= len(VERIFICATION_DELAYS) + 1:
                continue
            pending.append(
                SubscriptionWork(
                    str(attempt.workspace_id),
                    str(attempt.user_id),
                    str(attempt.obligation_id),
                    str(attempt.id),
                )
            )
        return pending
