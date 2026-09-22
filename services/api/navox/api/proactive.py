from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.api.auth import CurrentAccountDependency, DatabaseSession
from navox.db.models import ProactivePreference, ProactiveSignal
from navox.proactive.engine import (
    InvalidProactiveTimezone,
    default_preference,
    evaluate_workspace,
    mark_signals_surfaced,
    next_meeting_prep,
    record_briefing,
    timezone_for,
    visible_signals,
)

router = APIRouter(prefix="/proactive", tags=["proactive"])


class SignalResponse(BaseModel):
    id: UUID
    commitment_id: UUID | None
    signal_type: str
    status: str
    tier: str
    attention_score: int
    score_components: dict[str, int]
    what_happening: str
    why_matters: str
    suggested_capability: str | None
    last_evaluated_at: datetime
    last_surfaced_at: datetime | None
    surface_count: int
    snoozed_until: datetime | None


class BriefingResponse(BaseModel):
    request_id: UUID
    generated_at: datetime
    timezone: str
    headline: str
    notify_now: list[SignalResponse]
    briefing: list[SignalResponse]
    dashboard: list[SignalResponse]


class PreferenceResponse(BaseModel):
    timezone: str
    notifications_enabled: bool
    quiet_hours_start: str
    quiet_hours_end: str
    daily_briefing_hour: int
    notify_threshold: int
    briefing_threshold: int
    dashboard_threshold: int
    max_interruptions_per_day: int
    cooldown_minutes: int


class PreferenceUpdateRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)
    notifications_enabled: bool = True
    quiet_hours_start: str = Field(default="22:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    quiet_hours_end: str = Field(default="07:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    daily_briefing_hour: int = Field(default=8, ge=0, le=23)
    notify_threshold: int = Field(default=85, ge=1, le=100)
    briefing_threshold: int = Field(default=65, ge=1, le=100)
    dashboard_threshold: int = Field(default=40, ge=1, le=100)
    max_interruptions_per_day: int = Field(default=3, ge=0, le=20)
    cooldown_minutes: int = Field(default=240, ge=15, le=1440)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        timezone_for(value)
        return value

    @field_validator("notify_threshold")
    @classmethod
    def validate_notify_threshold(cls, value: int, info: object) -> int:
        return value

    def validate_order(self) -> None:
        if not (
            self.dashboard_threshold
            < self.briefing_threshold
            < self.notify_threshold
        ):
            raise ValueError(
                "Thresholds must satisfy dashboard < briefing < notify"
            )


class SnoozeRequest(BaseModel):
    until: datetime

    @field_validator("until")
    @classmethod
    def require_aware_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("until must include a timezone")
        if value <= datetime.now(UTC):
            raise ValueError("until must be in the future")
        return value


class MeetingRelatedResponse(BaseModel):
    id: UUID
    title: str
    status: str


class MeetingPrepResponse(BaseModel):
    commitment_id: UUID
    title: str
    starts_at: datetime
    minutes_until: int
    description: str | None
    related_commitments: list[MeetingRelatedResponse]
    prep_points: list[str]


def signal_response(signal: ProactiveSignal) -> SignalResponse:
    return SignalResponse(
        id=signal.id,
        commitment_id=signal.commitment_id,
        signal_type=signal.signal_type,
        status=signal.status,
        tier=signal.tier,
        attention_score=signal.attention_score,
        score_components=signal.score_components,
        what_happening=signal.what_happening,
        why_matters=signal.why_matters,
        suggested_capability=signal.suggested_capability,
        last_evaluated_at=signal.last_evaluated_at,
        last_surfaced_at=signal.last_surfaced_at,
        surface_count=signal.surface_count,
        snoozed_until=signal.snoozed_until,
    )


async def current_signal(
    database: AsyncSession,
    *,
    signal_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
) -> ProactiveSignal:
    signal = await database.scalar(
        select(ProactiveSignal).where(
            ProactiveSignal.id == signal_id,
            ProactiveSignal.user_id == user_id,
            ProactiveSignal.workspace_id == workspace_id,
        )
    )
    if signal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found")
    return signal


@router.get("/preferences", response_model=PreferenceResponse)
async def get_preferences(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> PreferenceResponse:
    preference = await default_preference(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
    )
    await database.commit()
    return PreferenceResponse(
        timezone=current_account.user.timezone,
        notifications_enabled=preference.notifications_enabled,
        quiet_hours_start=preference.quiet_hours_start,
        quiet_hours_end=preference.quiet_hours_end,
        daily_briefing_hour=preference.daily_briefing_hour,
        notify_threshold=preference.notify_threshold,
        briefing_threshold=preference.briefing_threshold,
        dashboard_threshold=preference.dashboard_threshold,
        max_interruptions_per_day=preference.max_interruptions_per_day,
        cooldown_minutes=preference.cooldown_minutes,
    )


@router.post("/preferences", response_model=PreferenceResponse)
async def update_preferences(
    payload: PreferenceUpdateRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> PreferenceResponse:
    try:
        payload.validate_order()
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    preference = await default_preference(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
    )
    current_account.user.timezone = payload.timezone
    preference.notifications_enabled = payload.notifications_enabled
    preference.quiet_hours_start = payload.quiet_hours_start
    preference.quiet_hours_end = payload.quiet_hours_end
    preference.daily_briefing_hour = payload.daily_briefing_hour
    preference.notify_threshold = payload.notify_threshold
    preference.briefing_threshold = payload.briefing_threshold
    preference.dashboard_threshold = payload.dashboard_threshold
    preference.max_interruptions_per_day = payload.max_interruptions_per_day
    preference.cooldown_minutes = payload.cooldown_minutes
    add_audit_event(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        event_type="proactive.preferences.updated",
        entity_type="user",
        entity_id=current_account.user.id,
        metadata={"timezone": payload.timezone},
        actor_type="user",
        actor_id=str(current_account.user.id),
    )
    await database.commit()
    return await get_preferences(current_account, database)


@router.post("/evaluate", response_model=list[SignalResponse])
async def evaluate(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    timezone_name: str | None = Query(default=None, alias="timezone"),
) -> list[SignalResponse]:
    selected_timezone = timezone_name or current_account.user.timezone
    try:
        signals = await evaluate_workspace(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            timezone_name=selected_timezone,
        )
    except InvalidProactiveTimezone as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return [signal_response(signal) for signal in signals]


@router.get("/briefing", response_model=BriefingResponse)
async def briefing(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    timezone_name: str | None = Query(default=None, alias="timezone"),
    request_id: UUID | None = Query(default=None),
) -> BriefingResponse:
    selected_timezone = timezone_name or current_account.user.timezone
    try:
        await evaluate_workspace(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            timezone_name=selected_timezone,
        )
    except InvalidProactiveTimezone as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    signals = await visible_signals(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
    )
    snapshot = await record_briefing(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        timezone_name=selected_timezone,
        request_id=request_id or uuid4(),
        signals=signals,
    )
    await mark_signals_surfaced(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        signals=signals,
        surface="briefing",
    )

    notify = [signal for signal in signals if signal.tier == "notify_now"]
    briefing_items = [signal for signal in signals if signal.tier == "briefing"]
    dashboard = [signal for signal in signals if signal.tier == "dashboard"]
    if signals:
        headline = (
            f"{len(signals)} item(s) matter right now; "
            f"{len(notify)} are important enough for immediate attention."
        )
    else:
        headline = "Nothing proactive needs surfacing right now."
    return BriefingResponse(
        request_id=snapshot.request_id,
        generated_at=snapshot.generated_at,
        timezone=selected_timezone,
        headline=headline,
        notify_now=[signal_response(signal) for signal in notify],
        briefing=[signal_response(signal) for signal in briefing_items],
        dashboard=[signal_response(signal) for signal in dashboard],
    )


@router.post("/signals/{signal_id}/dismiss", response_model=SignalResponse)
async def dismiss_signal(
    signal_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> SignalResponse:
    signal = await current_signal(
        database,
        signal_id=signal_id,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
    )
    if signal.status == "resolved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Resolved signals cannot be dismissed",
        )
    now = datetime.now(UTC)
    signal.status = "dismissed"
    signal.tier = "suppressed"
    signal.dismissed_at = now
    add_audit_event(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        event_type="proactive.signal.dismissed",
        entity_type="proactive_signal",
        entity_id=signal.id,
        actor_type="user",
        actor_id=str(current_account.user.id),
    )
    await database.commit()
    return signal_response(signal)


@router.post("/signals/{signal_id}/snooze", response_model=SignalResponse)
async def snooze_signal(
    signal_id: UUID,
    payload: SnoozeRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> SignalResponse:
    signal = await current_signal(
        database,
        signal_id=signal_id,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
    )
    if signal.status in {"resolved", "dismissed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only active signals can be snoozed",
        )
    signal.snoozed_until = payload.until
    signal.tier = "suppressed"
    add_audit_event(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        event_type="proactive.signal.snoozed",
        entity_type="proactive_signal",
        entity_id=signal.id,
        metadata={"until": payload.until.isoformat()},
        actor_type="user",
        actor_id=str(current_account.user.id),
    )
    await database.commit()
    return signal_response(signal)


@router.get("/meeting-prep", response_model=MeetingPrepResponse | None)
async def meeting_prep(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> MeetingPrepResponse | None:
    prep = await next_meeting_prep(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
    )
    if prep is None:
        return None
    return MeetingPrepResponse(
        commitment_id=prep.commitment_id,
        title=prep.title,
        starts_at=prep.starts_at,
        minutes_until=prep.minutes_until,
        description=prep.description,
        related_commitments=[
            MeetingRelatedResponse(id=item[0], title=item[1], status=item[2])
            for item in prep.related_commitments
        ],
        prep_points=list(prep.prep_points),
    )
