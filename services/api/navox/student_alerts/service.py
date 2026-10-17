"""Opt-in policy, ownership, and tenant-bound encrypted subscription storage."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.import_storage import cipher
from navox.core.settings import Settings
from navox.db.models import User, UserSession, Workspace, WorkspaceMembership
from navox.db.student_alerts import (
    StudentAlertOutbox,
    StudentAlertPreference,
    StudentPushSubscription,
)
from navox.student_alerts.schemas import AlertSettings, SubscriptionInput


class AlertError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def owner(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, *, lock: bool = False
) -> User | None:
    membership_query = (
        select(WorkspaceMembership)
        .join(Workspace)
        .where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.role == "owner",
            Workspace.workspace_type.in_(("personal", "PERSONAL")),
        )
    )
    if lock:
        membership_query = membership_query.with_for_update(of=WorkspaceMembership)
    if await database.scalar(membership_query) is None:
        return None
    query = select(User).where(User.id == user_id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    return cast(User | None, await database.scalar(query))


async def preference(
    database: AsyncSession, user_id: UUID, workspace_id: UUID
) -> StudentAlertPreference | None:
    return cast(
        StudentAlertPreference | None,
        await database.scalar(
            select(StudentAlertPreference)
            .where(
                StudentAlertPreference.user_id == user_id,
                StudentAlertPreference.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        ),
    )


def preference_settings(row: StudentAlertPreference | None) -> AlertSettings:
    if row is None:
        return AlertSettings()
    return AlertSettings.model_validate(
        {field: getattr(row, field) for field in AlertSettings.model_fields}
    )


def quiet_now(config: AlertSettings, timezone: str, now: datetime) -> bool:
    try:
        local = aware(now).astimezone(ZoneInfo(timezone)).strftime("%H:%M")
    except (ZoneInfoNotFoundError, ValueError):
        # An invalid timezone must not accidentally bypass quiet hours.
        return True
    start, end = config.quiet_start, config.quiet_end
    return (
        False
        if start == end
        else start <= local < end
        if start < end
        else local >= start or local < end
    )


async def save_settings(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, config: AlertSettings
) -> StudentAlertPreference:
    if await owner(database, user_id, workspace_id, lock=True) is None:
        raise AlertError("Personal workspace access is required", 403)
    row = await preference(database, user_id, workspace_id)
    if row is None:
        row = StudentAlertPreference(
            user_id=user_id, workspace_id=workspace_id, **config.model_dump()
        )
        database.add(row)
    else:
        for field, value in config.model_dump().items():
            setattr(row, field, value)
        row.revision += 1
    await database.execute(
        delete(StudentAlertOutbox).where(
            StudentAlertOutbox.user_id == user_id,
            StudentAlertOutbox.workspace_id == workspace_id,
            StudentAlertOutbox.state == "queued",
        )
    )
    await database.commit()
    return row


def seal_subscription(
    subscription: SubscriptionInput,
    settings: Settings,
    *,
    subscription_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    session_id: UUID,
) -> str:
    envelope = {
        "id": str(subscription_id),
        "user_id": str(user_id),
        "workspace_id": str(workspace_id),
        "session_id": str(session_id),
        "subscription": subscription.model_dump(),
    }
    return cipher(settings).encrypt(json.dumps(envelope, separators=(",", ":")).encode()).decode()


def reveal_subscription(row: StudentPushSubscription, settings: Settings) -> SubscriptionInput:
    try:
        data = json.loads(cipher(settings).decrypt(row.encrypted_subscription.encode()))
        if (data["id"], data["user_id"], data["workspace_id"], data["session_id"]) != tuple(
            map(str, (row.id, row.user_id, row.workspace_id, row.session_id))
        ):
            raise ValueError
        return SubscriptionInput.model_validate(data["subscription"])
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise AlertError("This device must enable notifications again", 409) from None


async def subscribe(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    session_id: UUID,
    subscription: SubscriptionInput,
    settings: Settings,
) -> StudentPushSubscription:
    if await owner(database, user_id, workspace_id, lock=True) is None:
        raise AlertError("Personal workspace access is required", 403)
    session = await database.scalar(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )
    if session is None:
        raise AlertError("Sign in again to enable notifications", 401)
    digest = sha256(subscription.endpoint.encode()).hexdigest()
    row = await database.scalar(
        select(StudentPushSubscription).where(StudentPushSubscription.endpoint_hash == digest)
    )
    if row is not None and (row.user_id != user_id or row.workspace_id != workspace_id):
        raise AlertError(
            "Turn off notifications for the previous account on this device first", 409
        )
    if row is None:
        count = await database.scalar(
            select(func.count())
            .select_from(StudentPushSubscription)
            .where(
                StudentPushSubscription.user_id == user_id,
                StudentPushSubscription.workspace_id == workspace_id,
            )
        )
        if (count or 0) >= 5:
            raise AlertError("Remove a saved device before adding another (maximum 5)", 409)
        row = StudentPushSubscription(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session_id,
            endpoint_hash=digest,
            encrypted_subscription="",
        )
        database.add(row)
    row.session_id = session_id
    row.encrypted_subscription = seal_subscription(
        subscription,
        settings,
        subscription_id=row.id,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
    )
    await database.commit()
    return row


async def unsubscribe(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, subscription_id: UUID
) -> None:
    if await owner(database, user_id, workspace_id, lock=True) is None:
        raise AlertError("Personal workspace access is required", 403)
    row = await database.scalar(
        select(StudentPushSubscription).where(
            StudentPushSubscription.id == subscription_id,
            StudentPushSubscription.user_id == user_id,
            StudentPushSubscription.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise AlertError("Saved device was not found", 404)
    await database.execute(
        delete(StudentAlertOutbox).where(
            StudentAlertOutbox.subscription_id == row.id, StudentAlertOutbox.state == "queued"
        )
    )
    await database.delete(row)
    await database.commit()


async def clear_student_alert_data(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID
) -> None:
    """Caller holds the shared User lock; forget only this owner's student alerts."""
    for model in (StudentAlertOutbox, StudentPushSubscription, StudentAlertPreference):
        await database.execute(
            delete(model).where(model.user_id == user_id, model.workspace_id == workspace_id)
        )
