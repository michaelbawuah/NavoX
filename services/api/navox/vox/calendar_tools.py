"""Owned meeting suggestions and exact calendar reviews for a current Vox call.

The model can prepare/read only. A separate host coordinator consumes current
student input after its deterministic read-back has completely played.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.models import UserSession
from navox.db.vox import VoxCall
from navox.db.vox_media import VoxMediaConnection
from navox.student import service as schedule
from navox.student_availability import service as availability
from navox.student_availability.ranked import ranked_suggestions
from navox.student_availability.schemas import AvailabilityPreview, AvailabilitySnapshot
from navox.student_availability.selector import (
    Purpose,
    SuggestionRequest,
    SuggestionResult,
    duration_for,
)
from navox.student_calendars import scheduling
from navox.student_calendars import service as calendars
from navox.student_calendars.operation_guard import guarded_operation
from navox.student_calendars.provider import GoogleCalendarWriteProvider
from navox.student_calendars.schemas import (
    CalendarEventApproval,
    CalendarEventPreview,
    CalendarEventView,
    CalendarTargetsRequest,
    CalendarTargetsView,
    CalendarTargetView,
)
from navox.student_planning import service as planning
from navox.vox.calendar_approval import CalendarApprovalCoordinator, PendingCalendarAction
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost, WorkerAuthority

CALENDAR_REVIEW_TOPIC = "navox.vox.calendar-review.v1"
SUGGEST_TOOL = "get_student_meeting_suggestions"
PREPARE_TOOL = "prepare_calendar_event"
UNDO_TOOL = "prepare_calendar_undo"


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class SuggestArguments(Arguments):
    purpose: Purpose
    date: str | None = None
    days: int = Field(ge=1, le=7)
    duration_minutes: int | None = Field(default=None, ge=5, le=480)


class EventArguments(Arguments):
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=500)
    start_at: str = Field(min_length=1, max_length=80)
    duration_minutes: int = Field(ge=5, le=480)
    calendar_name: str | None = Field(default=None, max_length=1024)
    account_name: str | None = Field(default=None, max_length=320)
    provider: Literal["google", "outlook", "icloud"] | None = None
    allow_study_displacement: bool = False

    @field_validator("title", "location", "calendar_name", "account_name")
    @classmethod
    def bounded_fields(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise ValueError("Use plain event fields")
        return value


def _same(left: str, right: str) -> bool:
    return (
        unicodedata.normalize("NFKC", left).casefold()
        == unicodedata.normalize("NFKC", right).casefold()
    )


def _signature(snapshot: AvailabilitySnapshot) -> str:
    # Observation/expiry clocks refresh; source meaning and exact intervals do not.
    value = {
        "revision": snapshot.revision.model_dump(mode="json"),
        "sources": [item.model_dump(mode="json") for item in snapshot.source_summary],
        "result": snapshot.result.model_dump(mode="json", exclude={"checked_at"}),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _when(start: datetime, end: datetime, timezone: str) -> str:
    zone = ZoneInfo(timezone)
    first, last = start.astimezone(zone), end.astimezone(zone)
    beginning = f"{first:%A, %B} {first.day}, {first.year}, {first:%I:%M %p}"
    ending = f"{last:%I:%M %p}"
    if first.date() != last.date():
        ending = f"{last:%A, %B} {last.day}, {last.year}, {ending}"
    if first.utcoffset() != last.utcoffset() or any(
        moment.replace(fold=0).utcoffset() != moment.replace(fold=1).utcoffset()
        for moment in (first, last)
    ):

        def offset(moment: datetime) -> str:
            value = moment.strftime("%z")
            return f" {moment.tzname()}, UTC{value[:3]}:{value[3:5]}"

        beginning += offset(first)
        ending += offset(last)
    return f"{beginning} to {ending}, {timezone}"


def readback(view: CalendarEventView, *, undo: bool = False) -> str:
    provider = {"google": "Google", "outlook": "Microsoft", "icloud": "Apple"}[view.provider]
    text = (
        "I'll read your private calendar review aloud now. "
        f"{'Remove only' if undo else 'Add'} the event “{view.title}” "
        f"{'from' if undo else 'to'} {provider} calendar “{view.calendar_label}”, "
        f"{_when(view.start_at, view.end_at, view.timezone)}. "
    )
    if view.location:
        text += f"Place: “{view.location}”. "
    details = view.model_dump(mode="json")
    moves = details.get("study_moves") or []
    if undo and details.get("study_changes_applied"):
        text += "The previously approved study moves will remain. "
    elif moves:
        for move in moves:
            before = _when(
                datetime.fromisoformat(move["before_start_at"]),
                datetime.fromisoformat(move["before_end_at"]),
                view.timezone,
            )
            after = _when(
                datetime.fromisoformat(move["after_start_at"]),
                datetime.fromisoformat(move["after_end_at"]),
                view.timezone,
            )
            text += (
                f"Move study “{move['title']}”, {move['minutes']} minutes, from "
                f"{before} to {after}. "
            )
        text += (
            "Study moves will be saved even if calendar creation fails; "
            "calendar Undo won't restore them. "
        )
    return text + (
        "Should I remove this exact event?" if undo else "Should I add this exact event?"
    )


def outcome(view: CalendarEventView) -> str:
    provider = {"google": "Google", "outlook": "Microsoft", "icloud": "Apple"}[view.provider]
    result = {
        "created": "Google confirmed this exact event was added.",
        "undone": "Google confirmed this exact calendar event is absent.",
        "failed": "Google rejected the event. It was not confirmed as added.",
        "changed": (
            "The external event changed. I cannot offer Undo for that version; "
            "review it in Google Calendar."
        ),
        "pending": "The event is still only a review; no calendar action was submitted.",
    }.get(
        view.status,
        "The calendar outcome is unconfirmed. Check its status; I will not submit it again.",
    )
    if view.model_dump().get("study_changes_applied"):
        result += " Your approved study moves were saved; calendar Undo does not restore them."
    if view.status == "created" and view.can_undo:
        result += " You can ask me to prepare Undo for this unchanged event."
    elif view.status == "created":
        result += " Exact unchanged-event Undo is unavailable; check the on-screen receipt."
    return result.replace("Google", provider)


class CalendarTools:
    def __init__(
        self,
        check: DatabaseAuthority,
        settings: Settings,
        approval: CalendarApprovalCoordinator,
        publish: Callable[[dict[str, object]], Awaitable[None]] | None = None,
        *,
        provider: GoogleCalendarWriteProvider | None = None,
        voice_available: bool = False,
    ):
        self.check, self.settings, self.approval = check, settings, approval
        self.publish, self.provider = publish, provider
        self.voice_available = voice_available
        self._binding: tuple[WorkerAuthority, str, UUID] | None = None
        self._last_created: UUID | None = None

    def definitions(self) -> list[dict[str, Any]]:
        if not self.settings.student_calendar_availability_enabled:
            return []
        definitions = [
            {
                "type": "function",
                "name": SUGGEST_TOOL,
                "strict": True,
                "description": (
                    "Find two ranked, buffered meeting options from this student's actual "
                    "calendars and confirmed routine. Use purpose lunch, coffee, office_hours, "
                    "project_meeting, study_session or other. Local date may be null for today; "
                    "days is 1–7. Use an explicit requested duration or null for purpose default; "
                    "ask when other has no duration. Other people's availability is never assumed. "
                    "Unknown coverage/travel is never free time. This cannot schedule or approve."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "purpose": {
                            "type": "string",
                            "enum": [
                                "lunch",
                                "coffee",
                                "office_hours",
                                "project_meeting",
                                "study_session",
                                "other",
                            ],
                        },
                        "date": {"type": ["string", "null"]},
                        "days": {"type": "integer", "minimum": 1, "maximum": 7},
                        "duration_minutes": {
                            "type": ["integer", "null"],
                            "minimum": 5,
                            "maximum": 480,
                        },
                    },
                    "required": ["purpose", "date", "days", "duration_minutes"],
                    "additionalProperties": False,
                },
            }
        ]
        if self.settings.student_calendar_writes_enabled:
            definitions.extend(
                [
                    {
                        "type": "function",
                        "name": PREPARE_TOOL,
                        "strict": True,
                        "description": (
                            "Prepare one exact eligible personal calendar event for host read-back "
                            "and the "
                            "student's next contextual answer. Ask for missing/ambiguous time or "
                            "target first. The host reads every exact field and affected study "
                            "move; do not claim anything was added or supply approval. Choose "
                            "calendar/account by exact known name, or null only when one eligible "
                            "choice exists. Provider only selects an already eligible owned grant; "
                            "it cannot grant permission or enable a provider. "
                            "Microsoft creation/Undo "
                            "remain unavailable. Apple requires separate current write consent. "
                            "Study displacement requests review, never applies it."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                                "location": {"type": "string", "maxLength": 500},
                                "start_at": {
                                    "type": "string",
                                    "description": (
                                        "An aware ISO8601 instant for the requested local time."
                                    ),
                                },
                                "duration_minutes": {
                                    "type": "integer",
                                    "minimum": 5,
                                    "maximum": 480,
                                },
                                "calendar_name": {"type": ["string", "null"]},
                                "account_name": {"type": ["string", "null"]},
                                "provider": {
                                    "type": ["string", "null"],
                                    "enum": ["google", "outlook", "icloud", None],
                                },
                                "allow_study_displacement": {"type": "boolean"},
                            },
                            "required": [
                                "title",
                                "location",
                                "start_at",
                                "duration_minutes",
                                "calendar_name",
                                "account_name",
                                "provider",
                                "allow_study_displacement",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "type": "function",
                        "name": UNDO_TOOL,
                        "strict": True,
                        "description": (
                            "Prepare exact Undo of the most recent verified event created in this "
                            "call. The host reads it and asks for a new contextual yes; this tool "
                            "cannot delete anything or undo study moves. No event identifier or "
                            "approval is accepted."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                            "additionalProperties": False,
                        },
                    },
                ]
            )
        return definitions

    async def _session(self) -> tuple[WorkerAuthority, str, UUID]:
        authority = await self.check()
        async with self.check.factory() as database:
            row = (
                await database.execute(
                    select(UserSession.token_hash, VoxCall.id)
                    .select_from(VoxMediaConnection)
                    .join(VoxCall, VoxCall.id == VoxMediaConnection.call_id)
                    .join(UserSession, UserSession.id == VoxCall.auth_session_id)
                    .where(
                        VoxMediaConnection.id == self.check.assignment.connection_id,
                        VoxCall.user_id == authority.user_id,
                        VoxCall.workspace_id == authority.workspace_id,
                        VoxMediaConnection.auth_session_id == UserSession.id,
                    )
                )
            ).one_or_none()
        if row is None or await self.check() != authority:
            raise WorkerAccessLost("The calendar call authority changed")
        binding = (authority, row[0], row[1])
        if self._binding is not None and self._binding != binding:
            raise WorkerAccessLost("The calendar call session changed")
        self._binding = binding
        return binding

    async def _guard(self) -> None:
        await self._session()

    async def _window(self, start: datetime | None, days: int, minutes: int) -> AvailabilityPreview:
        authority, token, _ = await self._session()
        async with self.check.factory() as database:
            await calendars.boundary(database, authority.user_id, authority.workspace_id, token)
            preferences = await planning.preferences(
                database, authority.user_id, authority.workspace_id
            )
            now = utc(await database_now(database))
            zone = ZoneInfo(preferences.timezone)
            first = (start or now).astimezone(zone).date()
            left = datetime.combine(first, time.min, zone).astimezone(UTC)
            right = datetime.combine(first + timedelta(days=days), time.min, zone).astimezone(UTC)
            await database.commit()
        return AvailabilityPreview(
            start_at=max(left, now),
            end_at=right,
            duration_minutes=minutes,
            preferences_revision=preferences.revision,
            expected_timezone=preferences.timezone,
            routine_confirmed=preferences.revision > 0,
            travel_assumption="unknown",
            include_short_slots=minutes < 30,
        )

    async def _search(self, payload: AvailabilityPreview) -> AvailabilitySnapshot:
        snapshot, _ = await self._search_with_scope(payload)
        return snapshot

    async def _search_with_scope(
        self, payload: AvailabilityPreview
    ) -> tuple[AvailabilitySnapshot, calendars.CalendarScope]:
        authority, token, _ = await self._session()
        async with self.check.factory() as database:
            original = await calendars.scope_snapshot(
                database, authority.user_id, authority.workspace_id, token
            )
            await database.commit()
        snapshot = await availability.preview(
            self.check.factory,
            self.settings,
            authority.user_id,
            authority.workspace_id,
            token,
            payload,
        )
        async with self.check.factory() as database:
            await calendars.check_scope(database, original, token)
            current = await availability._internal(
                database,
                authority.user_id,
                authority.workspace_id,
                payload,
                settings=self.settings,
            )
            now = await database_now(database)
            if (
                current.digest != snapshot.revision.student_revision
                or current.planning_revision != snapshot.revision.planning_revision
                or current.preferences_revision != snapshot.revision.preferences_revision
                or now < snapshot.checked_at
                or now >= snapshot.expires_at
            ):
                raise schedule.StudentError(
                    409, "Calendar or planning sources changed. Check again"
                )
            await database.commit()
        return snapshot, original

    async def _ranked(
        self,
        payload: AvailabilityPreview,
        query: SuggestArguments,
        snapshot: AvailabilitySnapshot,
        original: calendars.CalendarScope,
    ) -> SuggestionResult:
        authority, token, _ = await self._session()
        async with self.check.factory() as database:
            await calendars.check_scope(database, original, token)
            current = await availability._internal(
                database,
                authority.user_id,
                authority.workspace_id,
                payload,
                settings=self.settings,
            )
            now = await database_now(database)
            if (
                current.digest != snapshot.revision.student_revision
                or current.planning_revision != snapshot.revision.planning_revision
                or current.preferences_revision != snapshot.revision.preferences_revision
                or not snapshot.checked_at <= now < snapshot.expires_at
            ):
                raise schedule.StudentError(409, "Meeting sources changed. Check again")
            selection = await ranked_suggestions(
                database,
                authority.user_id,
                authority.workspace_id,
                snapshot,
                SuggestionRequest(
                    availability=snapshot.result,
                    now=now,
                    purpose=query.purpose,
                    duration_minutes=payload.duration_minutes,
                    count=2,
                ),
                allow_study_displacement=self.settings.student_planning_enabled,
            )
            await calendars.check_scope(database, original, token)
            after = await availability._internal(
                database,
                authority.user_id,
                authority.workspace_id,
                payload,
                settings=self.settings,
            )
            if after != current or await database_now(database) >= snapshot.expires_at:
                raise schedule.StudentError(409, "Meeting sources changed. Check again")
            await database.commit()
        return selection

    async def _publish(self, view: CalendarEventView, voice: bool = False) -> None:
        await self._guard()
        if self.publish:
            await self.publish(
                {
                    "kind": "calendar-event",
                    "event_id": str(view.id),
                    "status": view.status,
                    "voice_approval_available": voice,
                    "destination": "/connections#calendar",
                }
            )
        await self._guard()

    async def _target(
        self, query: EventArguments
    ) -> tuple[CalendarTargetsView, CalendarTargetView]:
        authority, token, _ = await self._session()
        async with self.check.factory() as database:
            view = await scheduling.scheduling_view(
                database, authority.user_id, authority.workspace_id, token, self.settings
            )
            await database.commit()
        available = (
            {"google"}
            if view.providers is None
            else {item.id for item in view.providers if item.availability == "available"}
        )
        grants = [
            g
            for g in view.grants
            if g.status == "connected"
            and g.provider in available
            and (query.provider is None or query.provider == g.provider)
            and (
                query.account_name is None
                or (g.account_label is not None and _same(g.account_label, query.account_name))
            )
        ]
        if len(grants) != 1:
            raise schedule.StudentError(
                409, "Choose one eligible connected scheduling account in Connections first"
            )
        targets = await scheduling.targets(
            self.check.factory,
            self.settings,
            authority.user_id,
            authority.workspace_id,
            token,
            CalendarTargetsRequest(
                generation=view.generation,
                revision=view.revision,
                request_id=uuid4(),
                grant_id=grants[0].id,
            ),
            provider=self.provider,
        )
        selected = [
            target
            for target in targets.targets
            if query.calendar_name is None or _same(target.label, query.calendar_name)
        ]
        if len(selected) != 1:
            raise schedule.StudentError(
                409, "Name one exact writable calendar from Connections first"
            )
        return targets, selected[0]

    async def _prepare(self, query: EventArguments) -> dict[str, Any]:
        self.approval.invalidate("new_event")
        authority, token, _ = await self._session()
        start = datetime.fromisoformat(query.start_at)
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError
        window = await self._window(start, 1, query.duration_minutes)
        if start + timedelta(minutes=query.duration_minutes) > window.end_at:
            window = await self._window(start, 2, query.duration_minutes)
        if (
            start < window.start_at
            or start + timedelta(minutes=query.duration_minutes) > window.end_at
        ):
            raise schedule.StudentError(422, "Choose one future event within the reviewed window")
        targets, target = await self._target(query)
        draft = CalendarEventPreview.model_validate(
            {
                "generation": targets.generation,
                "revision": targets.revision,
                "request_id": uuid4(),
                "grant_id": targets.grant_id,
                "calendar_id": target.id,
                "title": query.title,
                "location": query.location,
                "start_at": start,
                "end_at": start + timedelta(minutes=query.duration_minutes),
                "timezone": window.expected_timezone,
                "availability": window,
                **({"allow_study_displacement": True} if query.allow_study_displacement else {}),
            }
        )
        view = await scheduling.preview(
            self.check.factory,
            self.settings,
            authority.user_id,
            authority.workspace_id,
            token,
            draft,
            provider=self.provider,
        )
        reviewed_snapshot = await self._search(window)
        signature = _signature(reviewed_snapshot)

        async def validate() -> None:
            async with guarded_operation(self._guard):
                await self._validate_view(view)
                if _signature(await self._search(window)) != signature:
                    raise schedule.StudentError(409, "The reviewed availability changed")

        async def apply() -> str:
            async with guarded_operation(self._guard):
                result = await scheduling.approve(
                    self.check.factory,
                    self.settings,
                    authority.user_id,
                    authority.workspace_id,
                    token,
                    view.id,
                    self._command(view),
                    provider=self.provider,
                )
                if result.status == "created":
                    self._last_created = result.id
                await self._publish(result)
                return outcome(result)

        voice = self.voice_available and self.approval.prepare(
            PendingCalendarAction(
                str(view.id) + ":" + view.digest,
                readback(view),
                validate,
                apply,
                lambda: self._validate_readback(view, reviewed_snapshot, window),
            )
        )
        await self._publish(view, voice)
        return {
            "status": "review_required",
            "applied": False,
            "voice_approval_available": voice,
            "message": "The host will read the exact event for your next answer."
            if voice
            else (
                "The complete event is too long for voice approval. "
                "Review and approve it on screen in Connections."
            ),
        }

    @staticmethod
    def _command(view: CalendarEventView) -> CalendarEventApproval:
        return CalendarEventApproval(
            generation=view.generation,
            revision=view.calendar_revision,
            request_id=uuid4(),
            event_revision=view.revision,
            digest=view.digest,
            confirmed=True,
        )

    async def _validate_view(self, expected: CalendarEventView) -> None:
        authority, token, _ = await self._session()
        async with self.check.factory() as database:
            current = await scheduling.event_view(
                database,
                authority.user_id,
                authority.workspace_id,
                token,
                expected.id,
                self.settings,
            )
            await database.commit()
        self._compare_view(current, expected)

    @staticmethod
    def _compare_view(current: CalendarEventView, expected: CalendarEventView) -> None:
        observed, original = current.model_dump(), expected.model_dump()
        observed["expires_at"], original["expires_at"] = (
            utc(current.expires_at),
            utc(expected.expires_at),
        )
        if observed != original:
            raise schedule.StudentError(409, "The exact event review changed")

    async def _validate_readback(
        self,
        view: CalendarEventView,
        snapshot: AvailabilitySnapshot | None = None,
        payload: AvailabilityPreview | None = None,
    ) -> None:
        """One owner-serialized local proof; never refresh providers per frame."""
        async with guarded_operation(self._guard):
            authority, token, _ = await self._session()
            async with self.check.factory() as database:
                await schedule.owner(database, authority.user_id, authority.workspace_id, lock=True)
                current_view = await scheduling.event_view(
                    database,
                    authority.user_id,
                    authority.workspace_id,
                    token,
                    view.id,
                    self.settings,
                )
                self._compare_view(current_view, view)
                if snapshot is not None:
                    if payload is None:
                        raise schedule.StudentError(
                            409, "The private calendar review proof is unavailable"
                        )
                    current = await availability._internal(
                        database,
                        authority.user_id,
                        authority.workspace_id,
                        payload,
                        settings=self.settings,
                    )
                    if (
                        current.digest != snapshot.revision.student_revision
                        or current.planning_revision != snapshot.revision.planning_revision
                        or current.preferences_revision != snapshot.revision.preferences_revision
                        or await database_now(database) >= snapshot.expires_at
                    ):
                        raise schedule.StudentError(
                            409, "The private calendar review sources changed"
                        )
                await self._guard()
                await database.commit()

    async def _undo(self) -> dict[str, Any]:
        self.approval.invalidate("new_undo")
        if self._last_created is None:
            raise schedule.StudentError(
                409, "No verified event created in this call is available for Undo"
            )
        authority, token, _ = await self._session()
        async with self.check.factory() as database:
            view = await scheduling.event_view(
                database,
                authority.user_id,
                authority.workspace_id,
                token,
                self._last_created,
                self.settings,
            )
            await database.commit()
        if view.status != "created" or not view.can_undo:
            raise schedule.StudentError(409, "Exact Undo is unavailable; review calendar activity")

        async def validate() -> None:
            async with guarded_operation(self._guard):
                await self._validate_view(view)

        async def apply() -> str:
            async with guarded_operation(self._guard):
                result = await scheduling.undo(
                    self.check.factory,
                    self.settings,
                    authority.user_id,
                    authority.workspace_id,
                    token,
                    view.id,
                    self._command(view),
                    provider=self.provider,
                )
                await self._publish(result)
                self._last_created = None
                return outcome(result)

        voice = self.voice_available and self.approval.prepare(
            PendingCalendarAction(
                str(view.id) + ":undo:" + view.digest,
                readback(view, undo=True),
                validate,
                apply,
                lambda: self._validate_readback(view),
            )
        )
        await self._publish(view, voice)
        return {
            "status": "review_required",
            "applied": False,
            "voice_approval_available": voice,
            "message": "The host will read back exact Undo."
            if voice
            else "Review exact Undo on screen in Connections.",
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self._guard()
        try:
            async with guarded_operation(self._guard):
                if name not in {definition["name"] for definition in self.definitions()}:
                    raise ValueError
                if name == PREPARE_TOOL:
                    return await self._prepare(EventArguments.model_validate(arguments))
                if name == UNDO_TOOL:
                    Arguments.model_validate(arguments)
                    return await self._undo()
                query = SuggestArguments.model_validate(arguments)
                minutes = duration_for(query.purpose, query.duration_minutes)
                if minutes is None:
                    return {
                        "status": "needs_duration",
                        "changes_applied": False,
                        "message": "How many minutes should the meeting last?",
                    }
                selected = None
                if query.date:
                    day = date.fromisoformat(query.date)
                    if day.isoformat() != query.date:
                        raise ValueError
                    authority, _, _ = await self._session()
                    async with self.check.factory() as database:
                        prefs = await planning.preferences(
                            database, authority.user_id, authority.workspace_id
                        )
                    selected = datetime.combine(day, time.min, ZoneInfo(prefs.timezone))
                payload = await self._window(selected, query.days, minutes)
                snapshot, original = await self._search_with_scope(payload)
                selection = await self._ranked(payload, query, snapshot, original)
                return {
                    "status": selection.status,
                    "timezone": selection.timezone,
                    "duration_minutes": minutes,
                    "coverage": snapshot.result.status,
                    "expires_at": snapshot.expires_at.isoformat(),
                    "changes_applied": False,
                    "options": [
                        {
                            "option": index + 1,
                            "start_at": item.start_at.isoformat(),
                            "end_at": item.end_at.isoformat(),
                            "local_time": _when(item.start_at, item.end_at, selection.timezone),
                            "reasons": list(item.reasons),
                            "displaced_study_block_count": len(item.displaced_study_block_ids),
                            "requires_study_displacement_review": bool(
                                item.displaced_study_block_ids
                            ),
                        }
                        for index, item in enumerate(selection.suggestions)
                    ],
                    "message": (
                        "No time is reserved. Unknown sources or travel are not free time; "
                        "these options do not imply another person's agreement."
                    )
                    if selection.suggestions
                    else (
                        "No meeting or study-compatible option was confirmed in this bounded "
                        "check. Narrow the window or choose another duration; this does not "
                        "prove no feasible time exists."
                    ),
                }
        except schedule.StudentError as error:
            return {"status": "unavailable", "applied": False, "message": str(error)}
        except (ValidationError, ValueError, TypeError):
            return {
                "status": "invalid_arguments",
                "applied": False,
                "message": (
                    "Use an exact future date/time, supported duration and known calendar choice."
                ),
            }
        except (SQLAlchemyError, TimeoutError):
            self.approval.invalidate("source_error")
            return {
                "status": "unavailable",
                "applied": False,
                "message": "Calendar information could not be verified. Nothing new is confirmed.",
            }
