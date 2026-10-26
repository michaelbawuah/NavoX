"""Fresh owner-scoped availability preview; no events, titles or tokens are saved."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.connectors.sync_state import database_now, utc
from navox.core.credential_vault import CredentialVaultError
from navox.core.settings import Settings
from navox.db.models import Connection
from navox.db.student_planning import StudentPlanningState, StudentStudyBlock
from navox.providers.apple_availability import AppleAvailabilitySnapshot
from navox.providers.google_availability import (
    GoogleAvailabilityProvider,
    GoogleAvailabilitySnapshot,
)
from navox.providers.google_oauth import GoogleAccessTokenError, access_token_for_connection
from navox.providers.microsoft_availability import (
    MicrosoftAvailabilityProvider,
    MicrosoftAvailabilitySnapshot,
)
from navox.providers.microsoft_mail import MicrosoftMailError
from navox.student import service as schedule
from navox.student.occurrences import ClassProjectionIssue, project
from navox.student_availability.engine import compute_availability
from navox.student_availability.schemas import (
    AvailabilityPreview,
    AvailabilityRequest,
    AvailabilityRevisions,
    AvailabilitySnapshot,
    AvailabilitySourceSummary,
    BusyInterval,
    ProjectionUncertainty,
    SourceCoverage,
    StudyBlock,
)
from navox.student_calendars import apple, microsoft
from navox.student_calendars import service as calendars
from navox.student_planning import service as planning_service
from navox.student_planning.schemas import Preferences

TIMETABLE = "student-timetable"
STUDY = "student-study-plan"
MAX_ACCOUNTS_PER_CHECK = 8
SOURCE_PADDING = timedelta(minutes=250)


@dataclass(frozen=True)
class InternalSnapshot:
    digest: str
    timezone: str
    preferences: Preferences
    preferences_revision: int
    planning_revision: int
    busy: tuple[BusyInterval, ...]
    studies: tuple[StudyBlock, ...]
    uncertainties: tuple[ProjectionUncertainty, ...]


def _source_window(payload: AvailabilityPreview, timezone: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone)
    first = payload.start_at.astimezone(zone).date()
    last = (payload.end_at - timedelta(microseconds=1)).astimezone(zone).date()
    left = datetime.combine(first, time.min)
    right = datetime.combine(last + timedelta(days=1), time.min)
    # Unknown travel conservatively affects whole local days in the existing
    # engine. Include each requested day and the largest supported travel plus
    # meeting buffer on both edges, including ambiguous midnight offsets.
    return (
        min(left.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1))
        - SOURCE_PADDING,
        max(right.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1))
        + SOURCE_PADDING,
    )


async def _internal(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: AvailabilityPreview,
) -> InternalSnapshot:
    user = await schedule.owner(database, user_id, workspace_id)
    preferences = await planning_service.preferences(database, user_id, workspace_id)
    if (
        user.timezone != payload.expected_timezone
        or preferences.revision != payload.preferences_revision
    ):
        raise schedule.StudentError(
            409, "Your timezone or routine changed. Review it and check again"
        )
    zone = ZoneInfo(user.timezone)
    first = payload.start_at.astimezone(zone).date()
    last = (payload.end_at - timedelta(microseconds=1)).astimezone(zone).date()
    if payload.end_at - payload.start_at > timedelta(days=15) or (last - first).days >= 14:
        raise schedule.StudentError(422, "Choose at most 14 local days of availability")
    source_start, source_end = _source_window(payload, user.timezone)
    source = await schedule.state_for_window(
        database, user_id, workspace_id, source_start, source_end
    )
    issues: list[ClassProjectionIssue] = []
    occurrences, fixed, _, warnings = project(
        *source,
        start_at=source_start,
        end_at=source_end,
        include_cancelled=False,
        include_conflicts=False,
        class_issues=issues,
    )
    row = await database.scalar(
        select(StudentPlanningState)
        .where(
            StudentPlanningState.user_id == user_id,
            StudentPlanningState.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    needs_review = False
    if row is not None and row.window_end is not None:
        needs_review = (
            row.needs_review
            or row.accepted_source_hash is None
            or row.accepted_source_hash
            != await planning_service.stable_source_hash(database, user_id, workspace_id, row)
        )
    blocks = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.start_at < source_end,
                StudentStudyBlock.end_at > source_start,
            )
            .order_by(StudentStudyBlock.id)
            .limit(601)
            .execution_options(populate_existing=True)
        )
    )
    if len(blocks) > 600:
        raise schedule.StudentError(
            409, "Narrow the availability window to check every study block"
        )
    if blocks and (row is None or row.accepted_source_hash is None or row.window_end is None):
        needs_review = True
    busy = []
    for item in occurrences:
        travel = (
            item.walk_minutes
            if item.walk_minutes is not None
            else (0 if payload.travel_assumption == "zero" else None)
        )
        busy.append(
            BusyInterval(
                user_id=user_id,
                workspace_id=workspace_id,
                id=item.id,
                source_id=TIMETABLE,
                kind="class",
                start_at=item.start_at,
                end_at=item.end_at,
                travel_before_minutes=travel,
                # A saved walk to a class says nothing about return travel.
                travel_after_minutes=0 if payload.travel_assumption == "zero" else None,
            )
        )
    busy.extend(
        BusyInterval(
            user_id=user_id,
            workspace_id=workspace_id,
            id=item.id,
            source_id=TIMETABLE,
            kind="fixed",
            start_at=item.start_at,
            end_at=item.end_at,
            travel_required=True,
            travel_before_minutes=0 if payload.travel_assumption == "zero" else None,
            travel_after_minutes=0 if payload.travel_assumption == "zero" else None,
        )
        for item in fixed
    )
    uncertainties = [
        ProjectionUncertainty(
            user_id=user_id,
            workspace_id=workspace_id,
            source_id=TIMETABLE,
            start_at=issue.day_start,
            end_at=issue.day_end,
            reason="unresolved_time",
        )
        for issue in issues
    ]
    if warnings:
        # Fixed-block projection warnings have no per-record interval seam. The
        # full requested window remains uncertain instead of inventing a time.
        uncertainties.append(
            ProjectionUncertainty(
                user_id=user_id,
                workspace_id=workspace_id,
                source_id=TIMETABLE,
                start_at=payload.start_at,
                end_at=payload.end_at,
                reason="projection_warning",
            )
        )
    studies = tuple(
        StudyBlock(
            user_id=user_id,
            workspace_id=workspace_id,
            id=str(block.id),
            start_at=utc(block.start_at),
            end_at=utc(block.end_at),
            movable=True,
            calendar_backed=False,
            needs_review=needs_review,
        )
        for block in blocks
    )
    body = {
        "timezone": user.timezone,
        "preferences": preferences.model_dump(mode="json"),
        "schedule": [
            [
                item.model_dump(mode="json")
                for item in sorted(collection, key=lambda item: str(item.id))
            ]
            for collection in source
        ],
        "planning_revision": row.revision if row else 0,
        "needs_review": needs_review,
        "studies": [
            {
                "id": str(block.id),
                "revision": block.revision,
                "start_at": utc(block.start_at).isoformat(),
                "end_at": utc(block.end_at).isoformat(),
                "status": block.status,
            }
            for block in blocks
        ],
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return InternalSnapshot(
        digest,
        user.timezone,
        Preferences.model_validate(preferences.model_dump(exclude={"revision", "timezone"})),
        preferences.revision,
        row.revision if row else 0,
        tuple(busy),
        studies,
        tuple(uncertainties),
    )


async def _token(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    scope: calendars.CalendarScope,
    grant: calendars.CalendarGrantSnapshot,
    session_hash: str,
) -> str:
    async with factory() as database:
        await calendars.check_scope(database, scope, session_hash)
        original = await calendars.owned_connection(
            database, scope.user_id, scope.workspace_id, grant.connection_id
        )
        if original is None:
            raise GoogleAccessTokenError("Calendar authorization unavailable")
        detached = Connection(
            id=original.id,
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            provider="google",
            status="active",
            external_account_id=original.external_account_id,
            external_email=original.external_email,
            granted_scopes=list(original.granted_scopes),
            credential_reference=original.credential_reference,
        )
        await database.commit()
        token = await access_token_for_connection(database, connection=detached, settings=settings)
        await calendars.check_scope(database, scope, session_hash)
        original = await calendars.owned_connection(
            database, scope.user_id, scope.workspace_id, grant.connection_id
        )
        if original is None:
            raise GoogleAccessTokenError("Calendar authorization unavailable")
        retained = sorted(set(original.granted_scopes) & set(detached.granted_scopes))
        original.granted_scopes = retained
        original.access_token_expires_at = detached.access_token_expires_at
        original.last_checked_at = detached.last_checked_at
        await database.commit()
        if frozenset(retained) != grant.granted_scopes:
            raise schedule.StudentError(
                409, "Calendar permissions changed. Review them and check again"
            )
        return token


async def preview(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    payload: AvailabilityPreview,
    *,
    provider: GoogleAvailabilityProvider | None = None,
    microsoft_provider: MicrosoftAvailabilityProvider | None = None,
) -> AvailabilitySnapshot:
    async with factory() as database:
        scope = await calendars.scope_snapshot(database, user_id, workspace_id, session_hash)
        internal = await _internal(database, user_id, workspace_id, payload)
        started = await database_now(database)
        await database.commit()
    if len(scope.grants) > 198:
        raise schedule.StudentError(409, "Too many calendar permissions to check safely")
    provider = provider or GoogleAvailabilityProvider()
    microsoft_provider = microsoft_provider or MicrosoftAvailabilityProvider()
    source_start, source_end = _source_window(payload, internal.timezone)
    external: dict[
        UUID, GoogleAvailabilitySnapshot | MicrosoftAvailabilitySnapshot | AppleAvailabilitySnapshot
    ] = {}

    async def check() -> None:
        async with factory() as database:
            await calendars.check_scope(database, scope, session_hash)
            await database.commit()

    deadline = asyncio.timeout(45)
    try:
        async with deadline:
            for grant in scope.grants[:MAX_ACCOUNTS_PER_CHECK]:
                if (
                    grant.status != "active"
                    or grant.connection_status != "active"
                    or grant.credential_id is None
                    or grant.connection_id is None
                    or not set(calendars.required_scopes(grant.provider)).issubset(
                        grant.granted_scopes
                    )
                ):
                    continue
                try:
                    if grant.provider == "icloud":
                        async with factory() as apple_database:
                            external[grant.id] = await apple.read(
                                apple_database,
                                scope,
                                grant,
                                settings,
                                start_at=source_start,
                                end_at=source_end,
                                session_hash=session_hash,
                            )
                        continue
                    if grant.provider == "microsoft":
                        token = await microsoft.access(
                            factory, settings, scope, grant, session_hash
                        )
                    elif grant.provider == "google":
                        token = await _token(factory, settings, scope, grant, session_hash)
                    else:
                        continue
                except (GoogleAccessTokenError, MicrosoftMailError, CredentialVaultError):
                    continue
                reader = microsoft_provider if grant.provider == "microsoft" else provider
                external[grant.id] = await reader.collect(
                    access_token=token,
                    start_at=source_start,
                    end_at=source_end,
                    check=check,
                )
    except TimeoutError:
        if not deadline.expired():
            raise
    async with factory() as database:
        await calendars.check_scope(database, scope, session_hash)
        current = await _internal(database, user_id, workspace_id, payload)
        if current.digest != internal.digest:
            raise schedule.StudentError(409, "Your schedule or study plan changed. Check again")
        now = await database_now(database)
        expires = started + timedelta(seconds=60)
        if now >= expires:
            raise schedule.StudentError(409, "This availability check took too long. Check again")
        coverage = []
        summaries = []
        busy = list(current.busy)
        required = [TIMETABLE, STUDY]
        for source_id in required:
            coverage.append(
                SourceCoverage(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source_id=source_id,
                    state="complete",
                    start_at=payload.start_at,
                    end_at=payload.end_at,
                    observed_at=now,
                    fresh_until=expires,
                    revision=current.digest,
                )
            )
            summaries.append(
                AvailabilitySourceSummary(source_id=source_id, state="complete", calendar_count=0)
            )
        grant_sources = [(f"{grant.provider}-{grant.id.hex}", grant) for grant in scope.grants]
        if not grant_sources:
            required.append("google-calendar-availability")
            coverage.append(
                SourceCoverage(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source_id=required[-1],
                    state="unavailable",
                    start_at=payload.start_at,
                    end_at=payload.end_at,
                    observed_at=started,
                    fresh_until=expires,
                    revision=f"{scope.generation}:{scope.revision}",
                )
            )
            summaries.append(
                AvailabilitySourceSummary(
                    source_id=required[-1], state="unavailable", calendar_count=0
                )
            )
        for source_id, grant in grant_sources:
            required.append(source_id)
            value = external.get(grant.id)
            state = value.state if value else "unavailable"
            coverage.append(
                SourceCoverage(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source_id=source_id,
                    state=state,
                    start_at=payload.start_at,
                    end_at=payload.end_at,
                    observed_at=started,
                    fresh_until=expires,
                    revision=f"{scope.generation}:{scope.revision}:{grant.revision}",
                )
            )
            summaries.append(
                AvailabilitySourceSummary(
                    source_id=source_id,
                    state=state,
                    calendar_count=value.calendar_count if value else 0,
                )
            )
            if value:
                travel = 0 if payload.travel_assumption == "zero" else None
                busy.extend(
                    BusyInterval(
                        user_id=user_id,
                        workspace_id=workspace_id,
                        id=f"gb-{grant.id.hex}-{index}",
                        source_id=source_id,
                        kind="meeting",
                        start_at=item.start_at,
                        end_at=item.end_at,
                        travel_before_minutes=travel,
                        travel_after_minutes=travel,
                    )
                    for index, item in enumerate(value.busy)
                )
        if len(busy) > 10_000:
            raise schedule.StudentError(
                409, "Narrow the availability window to check every busy interval"
            )
        request = AvailabilityRequest(
            user_id=user_id,
            workspace_id=workspace_id,
            start_at=payload.start_at,
            end_at=payload.end_at,
            timezone=current.timezone,
            now=now,
            preferences=current.preferences,
            routine_confirmed=payload.routine_confirmed and current.preferences_revision > 0,
            required_source_ids=tuple(required),
            coverage=tuple(coverage),
            busy=tuple(busy),
            study_blocks=current.studies,
            uncertainties=current.uncertainties,
            duration_minutes=payload.duration_minutes,
            include_short_slots=payload.include_short_slots,
        )
        result = compute_availability(request)
        await database.commit()
        return AvailabilitySnapshot(
            input=request,
            result=result,
            checked_at=now,
            expires_at=expires,
            revision=AvailabilityRevisions(
                student_revision=current.digest,
                planning_revision=current.planning_revision,
                preferences_revision=current.preferences_revision,
                availability_generation=scope.generation,
                availability_revision=scope.revision,
            ),
            source_summary=tuple(summaries),
        )
