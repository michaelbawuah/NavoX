"""Pure source/clock certificates never turn begun or uncertain work into free time."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from navox.db.student_planning import StudentPlanningState
from navox.student import service as schedule
from navox.student_availability.engine import compute_availability
from navox.student_availability.schemas import (
    AvailabilityRequest,
    AvailabilityRevisions,
    AvailabilitySnapshot,
    AvailabilitySourceSummary,
    SourceCoverage,
    StudyBlock,
)
from navox.student_calendars import displacement
from navox.student_planning.schemas import BlockView, Preferences, TaskView


def clock_case(*, complete=True):
    now = datetime(2030, 1, 2, 10, tzinfo=UTC)
    user_id, workspace_id, task_id, block_id = (uuid4() for _ in range(4))
    block = BlockView(
        id=block_id,
        plan_id=uuid4(),
        task_id=task_id,
        title="Study",
        start_at=now + timedelta(seconds=10),
        end_at=now + timedelta(hours=1, seconds=10),
        minutes=60,
        focus_window=False,
        revision=1,
        status="planned",
        actual_minutes=0,
        outcome_at=None,
    )
    task = TaskView(
        id=task_id,
        title="Study",
        course_id=None,
        due_at=now.replace(hour=22),
        estimated_minutes=60,
        remaining_minutes=60,
        actual_minutes=0,
        difficulty=3,
        priority=3,
        course_weight=1,
        status="active",
        revision=1,
        source_item_id=None,
        source_revision=None,
    )
    request = AvailabilityRequest(
        user_id=user_id,
        workspace_id=workspace_id,
        start_at=now.replace(hour=7),
        end_at=now.replace(hour=23),
        timezone="UTC",
        now=now,
        preferences=Preferences(),
        routine_confirmed=True,
        required_source_ids=("fixture",),
        coverage=(
            SourceCoverage(
                user_id=user_id,
                workspace_id=workspace_id,
                source_id="fixture",
                state="complete" if complete else "partial",
                start_at=now.replace(hour=7),
                end_at=now.replace(hour=23),
                observed_at=now,
                fresh_until=now + timedelta(seconds=60),
                revision="v1",
            ),
        ),
        study_blocks=(
            StudyBlock(
                user_id=user_id,
                workspace_id=workspace_id,
                id=str(block_id),
                start_at=block.start_at,
                end_at=block.end_at,
                movable=True,
            ),
        ),
        duration_minutes=30,
    )
    snapshot = AvailabilitySnapshot(
        input=request,
        result=compute_availability(request),
        checked_at=now,
        expires_at=now + timedelta(seconds=60),
        revision=AvailabilityRevisions(
            student_revision="a" * 64,
            planning_revision=1,
            preferences_revision=1,
            availability_generation=0,
            availability_revision=0,
        ),
        source_summary=(
            AvailabilitySourceSummary(
                source_id="fixture", state="complete" if complete else "partial", calendar_count=0
            ),
        ),
    )
    state = StudentPlanningState(user_id=user_id, workspace_id=workspace_id, revision=1)
    context = displacement.Context(state, [block], {task_id: task}, "b" * 64, {})
    return now, snapshot, context


def test_certificate_rejects_study_that_started_during_a_fresh_snapshot():
    now, snapshot, context = clock_case()
    first, last = now + timedelta(minutes=30), now + timedelta(hours=1)
    assert displacement.certify(context, snapshot, first, last, now) is not None
    with pytest.raises(schedule.StudentError, match="already started"):
        displacement.certify(context, snapshot, first, last, now + timedelta(seconds=20))


def test_certificate_rejects_expiry_and_foreign_context():
    now, snapshot, context = clock_case()
    first, last = now + timedelta(minutes=30), now + timedelta(hours=1)
    with pytest.raises(schedule.StudentError):
        displacement.certify(context, snapshot, first, last, snapshot.expires_at)
    context.state.user_id = uuid4()
    with pytest.raises(schedule.StudentError):
        displacement.certify(context, snapshot, first, last, now)


def test_movable_work_is_not_an_override_of_unknown_provider_coverage():
    now, snapshot, context = clock_case(complete=False)
    with pytest.raises(schedule.StudentError):
        displacement.certify(
            context, snapshot, now + timedelta(minutes=30), now + timedelta(hours=1), now
        )
