"""Bounded, durable class-alert outbox; no model, tracking, or automatic sends of mail."""

import asyncio
import json
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from navox.core.settings import Settings, get_settings
from navox.db.models import UserSession
from navox.db.session import get_session_factory
from navox.db.student_alerts import (
    StudentAlertOutbox,
    StudentAlertPreference,
    StudentPushSubscription,
)
from navox.student.schemas import Occurrence
from navox.student.service import list_occurrences
from navox.student_alerts.push import configured, send_push
from navox.student_alerts.service import (
    AlertError,
    aware,
    owner,
    preference,
    preference_settings,
    quiet_now,
    reveal_subscription,
)

MAX_OWNERS = 100
MAX_DELIVERIES = 32


def alert_key(event: dict[str, str]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def current_events(
    events: list[dict[str, str]], occurrences: list[Occurrence], now: datetime
) -> list[dict[str, str]]:
    current = {
        (item.id, aware(item.start_at).isoformat()): item
        for item in occurrences
        if not item.cancelled and aware(item.start_at) > now
    }
    return [
        event
        for event in events
        if (item := current.get((event["id"], event["start_at"]))) is not None
        and (
            event["kind"] != "leave_now"
            or (
                item.walk_minutes is not None
                and event.get("walk_minutes") == str(item.walk_minutes)
            )
        )
    ]


async def queue_owner(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, now: datetime
) -> int:
    now = aware(now)
    user = await owner(database, user_id, workspace_id, lock=True)
    pref = await preference(database, user_id, workspace_id)
    if user is None or pref is None:
        return 0
    # Round-robin scan ordering makes the global bounded batch fair to owners
    # with sparse schedules. This is operational metadata, never a read effect.
    pref.last_scan_at = now
    config = preference_settings(pref)
    if not config.enabled or user.agent_paused or quiet_now(config, user.timezone, now):
        await database.commit()
        return 0
    subscriptions = list(
        await database.scalars(
            select(StudentPushSubscription)
            .join(UserSession)
            .where(
                StudentPushSubscription.user_id == user_id,
                StudentPushSubscription.workspace_id == workspace_id,
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .limit(5)
        )
    )
    if not subscriptions:
        await database.commit()
        return 0
    previous = await database.scalars(
        select(StudentAlertOutbox.occurrences)
        .where(
            StudentAlertOutbox.user_id == user_id,
            StudentAlertOutbox.workspace_id == workspace_id,
            StudentAlertOutbox.created_at >= now - timedelta(days=2),
        )
        .order_by(StudentAlertOutbox.created_at.desc(), StudentAlertOutbox.id.desc())
        .limit(1500)
    )
    consumed = {alert_key(event) for events in previous for event in events}
    occurrences = await list_occurrences(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        start_at=now,
        end_at=now + timedelta(hours=3),
    )
    candidates: list[tuple[datetime, dict[str, str]]] = []
    for occurrence in occurrences:
        if occurrence.cancelled or aware(occurrence.start_at) <= now:
            continue
        alert_times = []
        if config.next_class:
            alert_times.append(
                ("next_class", aware(occurrence.start_at) - timedelta(minutes=config.lead_minutes))
            )
        if config.leave_now and occurrence.walk_minutes is not None:
            alert_times.append(
                (
                    "leave_now",
                    aware(occurrence.start_at) - timedelta(minutes=occurrence.walk_minutes + 3),
                )
            )
        for kind, alert_at in alert_times:
            event = {
                "id": occurrence.id,
                "start_at": aware(occurrence.start_at).isoformat(),
                "kind": kind,
                "walk_minutes": str(occurrence.walk_minutes) if kind == "leave_now" else "",
            }
            if (
                now - timedelta(minutes=2) <= alert_at <= now + timedelta(minutes=5)
                and not quiet_now(config, user.timezone, alert_at)
                and alert_key(event) not in consumed
            ):
                candidates.append((alert_at, event))
    candidates.sort(key=lambda item: (item[0], alert_key(item[1])))
    if not candidates or candidates[0][0] > now:
        await database.commit()
        return 0
    # One visible bundle for alerts within five minutes; later members may be
    # shown up to five minutes early. No exact minute claim is put on lockscreen.
    first = candidates[0][0]
    events = [event for alert_at, event in candidates if alert_at <= first + timedelta(minutes=5)][
        :20
    ]
    digest = sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
    for subscription in subscriptions:
        database.add(
            StudentAlertOutbox(
                user_id=user_id,
                workspace_id=workspace_id,
                subscription_id=subscription.id,
                bundle_key=digest,
                preference_revision=pref.revision,
                occurrences=events,
                scheduled_for=now,
                expires_at=min(
                    now + timedelta(minutes=2),
                    min(datetime.fromisoformat(event["start_at"]) for event in events),
                ),
            )
        )
    await database.commit()
    return len(subscriptions)


async def prepare_delivery(
    database: AsyncSession, outbox_id: UUID, now: datetime, settings: Settings
) -> tuple[UUID, str, dict[str, str]] | None:
    now = aware(now)
    initial = await database.get(StudentAlertOutbox, outbox_id)
    if initial is None or initial.state != "queued":
        return None
    user = await owner(database, initial.user_id, initial.workspace_id, lock=True)
    row = await database.scalar(
        select(StudentAlertOutbox)
        .where(StudentAlertOutbox.id == outbox_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.state != "queued":
        return None
    pref = await preference(database, row.user_id, row.workspace_id)
    subscription = await database.scalar(
        select(StudentPushSubscription)
        .join(UserSession)
        .where(
            StudentPushSubscription.id == row.subscription_id,
            StudentPushSubscription.user_id == row.user_id,
            StudentPushSubscription.workspace_id == row.workspace_id,
            UserSession.user_id == row.user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
    )
    config = preference_settings(pref)
    blocked = (
        not configured(settings)
        or user is None
        or user.agent_paused
        or pref is None
        or not config.enabled
        or pref.revision != row.preference_revision
        or subscription is None
        or aware(row.expires_at) <= now
        or (user is not None and quiet_now(config, user.timezone, now))
    )
    if blocked:
        row.state = "suppressed"
        await database.commit()
        return None
    assert user is not None and subscription is not None
    current = await list_occurrences(
        database,
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        start_at=now,
        end_at=now + timedelta(hours=3),
    )
    events = current_events(row.occurrences, current, now)
    if not events:
        row.state = "superseded"
        await database.commit()
        return None
    try:
        zone = ZoneInfo(user.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        row.state = "suppressed"
        await database.commit()
        return None
    day_start = datetime.combine(now.astimezone(zone).date(), time.min, zone).astimezone(UTC)
    # Count bundles, not devices; all attempts (including uncertain outcomes)
    # reserve capacity. A crash cannot retry forever or exceed the daily cap.
    prior = (
        select(StudentAlertOutbox.bundle_key)
        .where(
            StudentAlertOutbox.user_id == row.user_id,
            StudentAlertOutbox.workspace_id == row.workspace_id,
            StudentAlertOutbox.attempted_at >= day_start,
        )
        .distinct()
    )
    attempted = set(await database.scalars(prior))
    if row.bundle_key not in attempted and len(attempted) >= config.daily_cap:
        row.state = "capped"
        await database.commit()
        return None
    try:
        reveal_subscription(subscription, settings)
    except AlertError:
        row.state = "failed"
        await database.commit()
        return None
    # Persist *before* network I/O. Ambiguous outcomes are intentionally never
    # replayed; the stable browser tag also collapses duplicate transport events.
    row.state = "attempted"
    row.attempted_at = now
    body = "An upcoming class is on your saved schedule. Open NavoX to check the time."
    if all(event["kind"] == "leave_now" for event in events):
        body = "Time to leave for an upcoming class, based on your saved walk time."
    payload = {
        "title": "NavoX · Class reminder",
        "body": body,
        "tag": f"class-{row.bundle_key[:26]}",
        "url": "/classes",
    }
    ciphertext = subscription.encrypted_subscription
    await database.commit()
    return subscription.id, ciphertext, payload


async def deliver_one(
    factory: async_sessionmaker[AsyncSession], outbox_id: UUID, now: datetime, settings: Settings
) -> str:
    async with factory() as database:
        prepared = await prepare_delivery(database, outbox_id, now, settings)
    if prepared is None:
        return "skipped"
    subscription_id, ciphertext, payload = prepared
    # Recheck revocation/settings after reserving and immediately before I/O.
    # Provider I/O never holds a database transaction or row lock.
    async with factory() as database:
        row = await database.get(StudentAlertOutbox, outbox_id)
        if row is None:
            return "revoked"
        user = await owner(database, row.user_id, row.workspace_id)
        pref = await preference(database, row.user_id, row.workspace_id)
        subscription = await database.scalar(
            select(StudentPushSubscription)
            .join(UserSession)
            .where(
                StudentPushSubscription.id == subscription_id,
                StudentPushSubscription.encrypted_subscription == ciphertext,
                UserSession.user_id == row.user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > datetime.now(UTC),
            )
        )
        if (
            user is None
            or user.agent_paused
            or pref is None
            or not pref.enabled
            or pref.revision != row.preference_revision
            or subscription is None
            or quiet_now(preference_settings(pref), user.timezone, datetime.now(UTC))
        ):
            row.state = "suppressed"
            await database.commit()
            return "revoked"
        rechecked_at = datetime.now(UTC)
        current = await list_occurrences(
            database,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            start_at=rechecked_at,
            end_at=rechecked_at + timedelta(hours=3),
        )
        if aware(row.expires_at) <= rechecked_at or not current_events(
            row.occurrences, current, rechecked_at
        ):
            row.state = "superseded"
            await database.commit()
            return "superseded"
        opened = reveal_subscription(subscription, settings)
        await database.rollback()
    try:
        status = await send_push(opened, payload, settings)
        state = (
            "accepted"
            if status in {200, 201, 202, 204}
            else "expired"
            if status in {404, 410}
            else "failed"
        )
    except Exception:
        # No exception text/URL is retained: push endpoints are credentials.
        state = "uncertain"
    async with factory() as database:
        row = await database.get(StudentAlertOutbox, outbox_id)
        if row is not None and row.state == "attempted":
            row.state = state
        if state == "expired":
            await database.execute(
                delete(StudentPushSubscription).where(
                    StudentPushSubscription.id == subscription_id,
                    StudentPushSubscription.encrypted_subscription == ciphertext,
                )
            )
        await database.commit()
    return state


async def reconcile_alerts(
    factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime | None = None
) -> int:
    current = aware(now or datetime.now(UTC))
    async with factory() as database:
        # Retain only seven days of content-free delivery state; revoked or
        # expired sessions immediately lose their encrypted endpoint and outbox.
        await database.execute(
            delete(StudentPushSubscription).where(
                StudentPushSubscription.session_id.in_(
                    select(UserSession.id).where(
                        (UserSession.revoked_at.is_not(None)) | (UserSession.expires_at <= current)
                    )
                )
            )
        )
        await database.execute(
            delete(StudentAlertOutbox).where(
                StudentAlertOutbox.created_at < current - timedelta(days=7)
            )
        )
        await database.commit()
        if not configured(settings):
            return 0
        owners = list(
            (
                await database.execute(
                    select(StudentAlertPreference.user_id, StudentAlertPreference.workspace_id)
                    .where(StudentAlertPreference.enabled.is_(True))
                    .order_by(
                        StudentAlertPreference.last_scan_at.asc().nulls_first(),
                        StudentAlertPreference.id,
                    )
                    .limit(MAX_OWNERS)
                )
            ).all()
        )
    for user_id, workspace_id in owners:
        async with factory() as database:
            await queue_owner(database, user_id, workspace_id, current)
    async with factory() as database:
        pending = list(
            await database.scalars(
                select(StudentAlertOutbox.id)
                .where(
                    StudentAlertOutbox.state == "queued",
                    StudentAlertOutbox.scheduled_for <= current,
                )
                .order_by(StudentAlertOutbox.scheduled_for, StudentAlertOutbox.id)
                .limit(MAX_DELIVERIES)
            )
        )
    semaphore = asyncio.Semaphore(8)

    async def deliver(identifier: UUID) -> str:
        async with semaphore:
            return await deliver_one(factory, identifier, current, settings)

    results = await asyncio.gather(
        *(deliver(identifier) for identifier in pending), return_exceptions=True
    )
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("Class reminder delivery deferred")
    return len(pending)


@activity.defn
async def reconcile_student_alerts_activity() -> int:
    return await reconcile_alerts(get_session_factory(), get_settings())
