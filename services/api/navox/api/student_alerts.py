"""Read-only notification status and explicitly requested settings/device changes."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from navox.api.auth import (
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
    hash_session_token,
    request_session_token,
)
from navox.db.models import UserSession
from navox.db.student_alerts import StudentPushSubscription
from navox.student_alerts.push import configured
from navox.student_alerts.schemas import (
    AlertSettings,
    AlertStatus,
    DeviceStatus,
    SubscriptionInput,
    SubscriptionResponse,
)
from navox.student_alerts.service import (
    AlertError,
    owner,
    preference,
    preference_settings,
    save_settings,
    subscribe,
    unsubscribe,
)

router = APIRouter(prefix="/student/notifications", tags=["student-notifications"])


@router.get("", response_model=AlertStatus)
async def status(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AlertStatus:
    account = current_account
    if await owner(database, account.user.id, account.workspace.id) is None:
        raise HTTPException(403, "Personal workspace access is required")
    rows = await database.scalars(
        select(StudentPushSubscription)
        .join(UserSession)
        .where(
            StudentPushSubscription.user_id == account.user.id,
            StudentPushSubscription.workspace_id == account.workspace.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
        .order_by(StudentPushSubscription.created_at)
        .limit(5)
    )
    available = configured(settings)
    return AlertStatus(
        configured=available,
        public_key=settings.student_push_vapid_public_key if available else None,
        settings=preference_settings(
            await preference(database, account.user.id, account.workspace.id)
        ),
        devices=[DeviceStatus(id=row.id, endpoint_hash=row.endpoint_hash) for row in rows],
        timezone=account.user.timezone,
        notice=(
            "Push delivery depends on your browser and device. "
            "This is a class reminder, not an alarm."
        )
        if available
        else (
            "Class notifications are not available on this deployment yet. "
            "You can still use your class schedule."
        ),
    )


@router.put("/settings", response_model=AlertSettings)
async def update_settings(
    payload: AlertSettings,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AlertSettings:
    if payload.enabled and not configured(settings):
        raise HTTPException(503, "Class notifications are not configured yet")
    try:
        row = await save_settings(
            database, current_account.user.id, current_account.workspace.id, payload
        )
    except AlertError as error:
        raise HTTPException(error.status_code, str(error)) from error
    return preference_settings(row)


@router.post("/subscriptions", response_model=SubscriptionResponse, status_code=201)
async def add_device(
    payload: SubscriptionInput,
    request: Request,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SubscriptionResponse:
    if not configured(settings):
        raise HTTPException(503, "Class notifications are not configured yet")
    token = request_session_token(request, settings)
    session_id = await database.scalar(
        select(UserSession.id).where(
            UserSession.token_hash == hash_session_token(token or ""),
            UserSession.user_id == current_account.user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )
    if session_id is None:
        raise HTTPException(401, "Sign in again to enable notifications")
    try:
        row = await subscribe(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            session_id=session_id,
            subscription=payload,
            settings=settings,
        )
    except AlertError as error:
        raise HTTPException(error.status_code, str(error)) from error
    except IntegrityError as error:
        await database.rollback()
        raise HTTPException(409, "This device changed. Refresh notification settings.") from error
    return SubscriptionResponse(id=row.id)


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def remove_device(
    subscription_id: UUID, current_account: CurrentAccountDependency, database: DatabaseSession
) -> Response:
    try:
        await unsubscribe(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            subscription_id=subscription_id,
        )
    except AlertError as error:
        raise HTTPException(error.status_code, str(error)) from error
    return Response(status_code=204)
