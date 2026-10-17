"""Fresh manual reviews. Reads never create state, change a plan or call a provider."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.student import StudentCourse, StudentMeetingException
from navox.db.student_email import EmailTaskSource
from navox.db.student_goals import StudentGoal, StudentGoalCheckIn
from navox.db.student_planning import StudentStudyBlock, StudentTask
from navox.student import service as schedule
from navox.student.occurrences import ClassProjectionIssue, local_moment, project
from navox.student.schemas import FixedBlockView
from navox.student_coach import sources
from navox.student_coach.schemas import (
    CoachReview,
    GoalFact,
    GoalSection,
    Kind,
    Notice,
    OutcomeSection,
    OutcomeTotals,
    Question,
    ScheduleSection,
    TaskFact,
    Window,
    WorkSection,
    WorkTotals,
)
from navox.student_goals import service as goal_service
from navox.student_goals.schemas import MAX_ACTIVE, MAX_CHECKINS, MAX_GOALS
from navox.student_planning.schemas import MAX_TASKS

SourceKind = Literal["manual", "lms", "email"]


def enabled(settings: Settings, flag: str) -> bool:
    return getattr(settings, flag, False) is True


def window(start: date, days: int, zone: ZoneInfo, now: datetime) -> Window:
    return Window(
        starts_at=datetime.combine(start, time.min, zone).astimezone(UTC),
        ends_at=datetime.combine(start + timedelta(days=days), time.min, zone).astimezone(UTC),
        observed_through=now,
    )


def add_notice(notices: list[Notice], notice: Notice) -> None:
    if not any(item.code == notice.code for item in notices):
        notices.append(notice)


def fixed_gap_in_window(blocks: list[FixedBlockView], selected: Window) -> bool:
    # The shared projector also scans neighboring days. Its untyped warnings
    # cannot establish that a missing occurrence belongs to this review period.
    for block in blocks:
        zone = ZoneInfo(block.timezone)
        day = max(block.term_start, selected.starts_at.astimezone(zone).date())
        last = min(block.term_end, selected.ends_at.astimezone(zone).date())
        while day <= last:
            day_start = datetime.combine(day, time.min, zone).astimezone(UTC)
            day_end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
            if (
                day.weekday() in block.weekdays
                and day_start < selected.ends_at
                and day_end > selected.starts_at
            ):
                start = local_moment(day, block.start_time, block.timezone)
                end = local_moment(day, block.end_time, block.timezone)
                if start is None or end is None or end <= start:
                    return True
            day += timedelta(days=1)
    return False


async def timetable(
    database: AsyncSession, user: UUID, workspace: UUID, selected: Window, notices: list[Notice]
) -> ScheduleSection:
    exception_count = await database.scalar(
        select(func.count())
        .select_from(StudentMeetingException)
        .where(
            StudentMeetingException.user_id == user,
            StudentMeetingException.workspace_id == workspace,
            StudentMeetingException.date >= selected.starts_at.date() - timedelta(days=2),
            StudentMeetingException.date <= selected.ends_at.date() + timedelta(days=2),
        )
    )
    if exception_count is None or exception_count >= 10000:
        add_notice(
            notices,
            Notice(
                code="schedule_limit",
                message="Review the saved timetable before continuing.",
                review_href="/classes",
            ),
        )
        return ScheduleSection(
            status="unavailable",
            window=selected,
            classes_complete=False,
            fixed_blocks_complete=False,
        )
    data = await schedule.state_for_window(
        database, user, workspace, selected.starts_at, selected.ends_at
    )
    issues: list[ClassProjectionIssue] = []
    classes, fixed, _, _ = project(
        *data,
        start_at=selected.starts_at,
        end_at=selected.ends_at,
        include_cancelled=False,
        include_conflicts=False,
        class_issues=issues,
    )
    classes_complete = not issues and len(classes) <= 250
    fixed_complete = not fixed_gap_in_window(data[1], selected) and len(fixed) <= 100
    if issues:
        dates = ", ".join(sorted({item.day.isoformat() for item in issues}))
        add_notice(
            notices,
            Notice(
                code="class_clock_change",
                message=f"A saved class needs a one-day clock-change correction on {dates}. "
                "The class count is incomplete.",
                review_href="/classes",
            ),
        )
    if not fixed_complete:
        add_notice(
            notices,
            Notice(
                code="fixed_blocks_incomplete",
                message="A saved fixed block needs a clock-change correction or exceeds "
                "the review limit. Its count is incomplete.",
                review_href="/classes",
            ),
        )
    if len(classes) > 250:
        add_notice(
            notices,
            Notice(
                code="classes_limit",
                message="Only the first 250 saved class occurrences can be reviewed here. "
                "Narrow the timetable to review more.",
                review_href="/classes",
            ),
        )
    return ScheduleSection(
        status="complete" if classes_complete and fixed_complete else "incomplete",
        window=selected,
        classes=classes[:250],
        fixed_blocks=fixed[:100],
        classes_complete=classes_complete,
        fixed_blocks_complete=fixed_complete,
        class_count=len(classes) if classes_complete else None,
        fixed_block_count=len(fixed) if fixed_complete else None,
        next_class=next(
            (item for item in classes if item.start_at >= selected.observed_through), None
        )
        if classes_complete
        else None,
    )


@dataclass
class Inputs:
    eligible_ids: set[UUID]
    valid: dict[UUID, SourceKind]
    tasks: list[StudentTask]
    invalid_active: bool = False
    available: bool = True


async def task_inputs(
    database: AsyncSession, settings: Settings, user: UUID, workspace: UUID, notices: list[Notice]
) -> Inputs:
    # Classify without fetching a disabled source's copied title/deadline or payload.
    metadata = list(
        await database.scalars(
            select(StudentTask)
            .where(StudentTask.user_id == user, StudentTask.workspace_id == workspace)
            .options(
                load_only(
                    StudentTask.id,
                    StudentTask.course_id,
                    StudentTask.status,
                    StudentTask.source_item_id,
                    StudentTask.source_identity,
                    StudentTask.source_revision,
                    raiseload=True,
                )
            )
            .order_by(StudentTask.id)
            .limit(MAX_TASKS + 1)
            .execution_options(populate_existing=True)
        )
    )
    if len(metadata) > MAX_TASKS:
        add_notice(
            notices,
            Notice(
                code="task_limit",
                message="The saved task limit needs review. "
                "Task and progress totals are unavailable.",
                review_href="/planner",
            ),
        )
        return Inputs(set(), {}, [], available=False)
    course_ids = {item.course_id for item in metadata if item.course_id is not None}
    active_courses = (
        set(
            await database.scalars(
                select(StudentCourse.id).where(
                    StudentCourse.id.in_(course_ids),
                    StudentCourse.user_id == user,
                    StudentCourse.workspace_id == workspace,
                    StudentCourse.archived.is_(False),
                )
            )
        )
        if course_ids
        else set()
    )
    metadata = [
        item for item in metadata if item.course_id is None or item.course_id in active_courses
    ]
    identities = (
        set(
            await database.scalars(
                select(EmailTaskSource.task_id).where(
                    EmailTaskSource.user_id == user,
                    EmailTaskSource.workspace_id == workspace,
                    EmailTaskSource.task_id.in_([item.id for item in metadata]),
                )
            )
        )
        if metadata
        else set()
    )
    valid: dict[UUID, SourceKind] = {}
    invalid_active = False
    for item in metadata:
        kind: SourceKind = (
            "lms"
            if item.source_item_id is not None
            else (
                "email" if item.source_identity is not None or item.id in identities else "manual"
            )
        )
        current = kind == "manual"
        allowed = enabled(
            settings, "student_lms_feeds_enabled" if kind == "lms" else "student_email_enabled"
        )
        if kind == "lms" and allowed:
            current = await sources.lms_current(database, item, user, workspace)
        elif kind == "email" and allowed:
            current = await sources.email_current(database, item, user, workspace)
        if current:
            valid[item.id] = kind
        else:
            invalid_active |= item.status == "active"
            add_notice(
                notices,
                Notice(
                    code="task_source_unavailable",
                    message="Some task sources are disabled, changed or unavailable. "
                    "Their details are omitted until reviewed.",
                    review_href="/planner",
                ),
            )
    rows = (
        list(
            await database.scalars(
                select(StudentTask)
                .where(
                    StudentTask.user_id == user,
                    StudentTask.workspace_id == workspace,
                    StudentTask.id.in_(valid),
                )
                .execution_options(populate_existing=True)
            )
        )
        if valid
        else []
    )
    return Inputs({item.id for item in metadata}, valid, rows, invalid_active)


def work(inputs: Inputs, selected: Window) -> WorkSection:
    if not inputs.available:
        return WorkSection(status="unavailable", window=selected)
    active = [
        item for item in inputs.tasks if item.status == "active" and item.remaining_minutes > 0
    ]
    known = WorkTotals(
        active_tasks=len(active),
        overdue_tasks=sum(
            item.due_at is not None and utc(item.due_at) < selected.observed_through
            for item in active
        ),
        due_in_window_tasks=sum(
            item.due_at is not None and selected.starts_at <= utc(item.due_at) < selected.ends_at
            for item in active
        ),
    )
    active.sort(
        key=lambda item: (
            utc(item.due_at) if item.due_at else datetime.max.replace(tzinfo=UTC),
            -item.priority,
            str(item.id),
        )
    )
    return WorkSection(
        status="incomplete" if inputs.invalid_active else "complete",
        window=selected,
        totals=None if inputs.invalid_active else known,
        known_totals=known,
        has_more=len(active) > 20,
        items=[
            TaskFact(
                id=item.id,
                revision=item.revision,
                title=item.title,
                course_id=item.course_id,
                source_kind=inputs.valid[item.id],
                due_at=utc(item.due_at) if item.due_at else None,
                remaining_minutes=item.remaining_minutes,
                priority=item.priority,
            )
            for item in active[:20]
        ],
    )


async def outcomes(
    database: AsyncSession, user: UUID, workspace: UUID, inputs: Inputs, selected: Window
) -> OutcomeSection:
    if not inputs.available:
        return OutcomeSection(status="unavailable", window=selected)
    rows = (
        list(
            await database.execute(
                select(
                    StudentStudyBlock.task_id,
                    StudentStudyBlock.status,
                    func.count().label("quantity"),
                    func.sum(StudentStudyBlock.actual_minutes).label("minutes"),
                )
                .join(StudentTask, StudentTask.id == StudentStudyBlock.task_id)
                .where(
                    StudentStudyBlock.user_id == user,
                    StudentStudyBlock.workspace_id == workspace,
                    StudentTask.user_id == user,
                    StudentTask.workspace_id == workspace,
                    StudentTask.id.in_(inputs.eligible_ids),
                    StudentStudyBlock.outcome_at >= selected.starts_at,
                    StudentStudyBlock.outcome_at < selected.ends_at,
                    StudentStudyBlock.outcome_at <= selected.observed_through,
                    StudentStudyBlock.status.in_(["done", "took_longer", "skipped"]),
                )
                .group_by(StudentStudyBlock.task_id, StudentStudyBlock.status)
                .limit(MAX_TASKS * 3 + 1)
            )
        )
        if inputs.eligible_ids
        else []
    )
    known = OutcomeTotals()
    complete = len(rows) <= MAX_TASKS * 3
    for row in rows[: MAX_TASKS * 3]:
        if row.task_id not in inputs.valid:
            complete = False
            continue
        if row.status == "done":
            known.done_blocks += row.quantity
        elif row.status == "took_longer":
            known.took_longer_blocks += row.quantity
        else:
            known.skipped_blocks += row.quantity
        known.recorded_minutes += row.minutes or 0
    return OutcomeSection(
        status="complete" if complete else "incomplete",
        window=selected,
        totals=known if complete else None,
        known_totals=known,
    )


async def goals(
    database: AsyncSession,
    settings: Settings,
    user: UUID,
    workspace: UUID,
    timezone: str,
    now: datetime,
    notices: list[Notice],
) -> GoalSection:
    if not enabled(settings, "student_goals_enabled"):
        return GoalSection(status="disabled")
    rows = list(
        await database.scalars(
            select(StudentGoal)
            .where(
                StudentGoal.user_id == user,
                StudentGoal.workspace_id == workspace,
            )
            .order_by(StudentGoal.created_at, StudentGoal.id)
            .limit(MAX_GOALS + 1)
            .execution_options(populate_existing=True)
        )
    )
    active = [item for item in rows if item.status == "active"]
    reports = (
        list(
            await database.scalars(
                select(StudentGoalCheckIn)
                .where(
                    StudentGoalCheckIn.user_id == user,
                    StudentGoalCheckIn.workspace_id == workspace,
                    StudentGoalCheckIn.goal_id.in_([item.id for item in active]),
                    StudentGoalCheckIn.recorded_at <= now,
                )
                .order_by(StudentGoalCheckIn.on_date.desc())
                .limit(MAX_CHECKINS + 1)
                .execution_options(populate_existing=True)
            )
        )
        if active
        else []
    )
    try:
        if len(rows) > MAX_GOALS or len(active) > MAX_ACTIVE or len(reports) > MAX_CHECKINS:
            raise schedule.StudentError(409, "Goal limit needs review")
        facts = []
        for row in active:
            view = goal_service.view(row, reports, settings, timezone, now)
            facts.append(
                GoalFact(
                    id=row.id,
                    revision=row.revision,
                    title=view.title,
                    category=view.category,
                    tracking=view.tracking,
                    unit=view.unit,
                    cadence=view.cadence,
                    starts_on=view.starts_on,
                    ends_on=view.ends_on,
                    progress=view.progress,
                )
            )
        return GoalSection(status="complete", items=facts)
    except (schedule.StudentError, ValueError):
        add_notice(
            notices,
            Notice(
                code="goals_unavailable",
                message="Saved goal reports could not be verified. "
                "Review Goals before using their progress.",
                review_href="/goals",
            ),
        )
        return GoalSection(status="unavailable")


def question(review: CoachReview) -> Question:
    if any(
        item.code
        in {"class_clock_change", "fixed_blocks_incomplete", "schedule_limit", "classes_limit"}
        for item in review.notices
    ):
        return Question(
            code="review_schedule",
            text="Would you like to review your saved timetable?",
            review_href="/classes",
        )
    if any(item.code == "task_source_unavailable" for item in review.notices):
        return Question(
            code="review_task_sources",
            text="Would you like to review your saved task sources?",
            review_href="/planner",
        )
    if review.kind in {"evening", "weekly"} and any(
        item.starts_on <= review.local_date
        and (item.ends_on is None or review.local_date <= item.ends_on)
        and item.progress.days_reported == 0
        for item in review.goals.items
    ):
        return Question(
            code="goal_checkin",
            text="Would you like to record a goal check-in?",
            review_href="/goals",
        )
    if review.work.items:
        return Question(
            code="choose_task",
            text="Would you like to choose a saved task to focus on?",
            review_href="/planner",
        )
    return Question(
        code="review_saved_day",
        text="Would you like to review your saved timetable?",
        review_href="/classes",
    )


async def review(
    database: AsyncSession, settings: Settings, user_id: UUID, workspace_id: UUID, kind: Kind
) -> CoachReview:
    # Every domain writer/reset shares this lock. No connector/feed locks follow it.
    # Suppress autoflush so even a caller's unrelated pending state cannot be written.
    with database.no_autoflush:
        user = await schedule.owner(database, user_id, workspace_id, lock=True)
        now = utc(await database_now(database))
        try:
            zone = ZoneInfo(user.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise schedule.StudentError(
                409, "Review your account time zone before using day reviews"
            ) from None
        today = now.astimezone(zone).date()
        monday = today - timedelta(days=today.weekday())
        selected = window(
            monday if kind == "weekly" else today, 7 if kind == "weekly" else 1, zone, now
        )
        outlook = window(
            monday + timedelta(days=7) if kind == "weekly" else today + timedelta(days=1),
            7 if kind == "weekly" else 1,
            zone,
            now,
        )
        notices: list[Notice] = []
        day_schedule = await timetable(database, user_id, workspace_id, selected, notices)
        future = await timetable(database, user_id, workspace_id, outlook, notices)
        if enabled(settings, "student_planning_enabled"):
            inputs = await task_inputs(database, settings, user_id, workspace_id, notices)
            task_section = work(inputs, selected)
            outcome_section = await outcomes(database, user_id, workspace_id, inputs, selected)
        else:
            task_section = WorkSection(status="disabled", window=selected)
            outcome_section = OutcomeSection(status="disabled", window=selected)
        goal_section = await goals(
            database, settings, user_id, workspace_id, user.timezone, now, notices
        )
        lines = []
        period = "this week" if kind == "weekly" else "today"
        if day_schedule.class_count is not None:
            noun = "class" if day_schedule.class_count == 1 else "classes"
            verb = "is" if day_schedule.class_count == 1 else "are"
            lines.append(f"{day_schedule.class_count} {noun} {verb} saved for {period}.")
        if task_section.totals is not None:
            noun = "task" if task_section.totals.due_in_window_tasks == 1 else "tasks"
            deadline = (
                "has a deadline"
                if task_section.totals.due_in_window_tasks == 1
                else "have deadlines"
            )
            lines.append(
                f"{task_section.totals.due_in_window_tasks} saved {noun} {deadline} {period}."
            )
        if outcome_section.known_totals is not None and outcome_section.known_totals.done_blocks:
            noun = "block" if outcome_section.known_totals.done_blocks == 1 else "blocks"
            prefix = (
                "Among currently verified task sources, you recorded"
                if outcome_section.status == "incomplete"
                else "You recorded"
            )
            lines.append(
                f"{prefix} {outcome_section.known_totals.done_blocks} Done study {noun} {period}."
            )
        lines.append("You can choose one next step when you're ready.")
        result = CoachReview(
            kind=kind,
            server_now=now,
            timezone=user.timezone,
            local_date=today,
            window=selected,
            schedule=day_schedule,
            outlook=future,
            work=task_section,
            outcomes=outcome_section,
            goals=goal_section,
            summary_lines=lines,
            notices=notices,
            question=None,
        )
        result.question = question(result)
        return result
