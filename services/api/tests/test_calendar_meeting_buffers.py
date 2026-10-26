"""Exact-write approval cannot bypass the new meeting's own preparation buffer."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from navox.student.service import StudentError
from navox.student_availability.schemas import (
    AvailabilityPreview,
    AvailabilityRequest,
    AvailabilityResult,
    AvailabilityRevisions,
    AvailabilitySnapshot,
    AvailabilitySourceSummary,
    Opening,
    SourceCoverage,
)
from navox.student_calendars import scheduling
from navox.student_calendars.schemas import CalendarEventPreview
from navox.student_planning.schemas import Preferences

NOW = datetime(2026, 10, 26, 10, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "left,right,offset,allowed",
    [
        (60, 120, 60, False),  # Event immediately after a shift.
        (60, 120, 90, False),  # Event immediately before sleep/a fixed commitment.
        (60, 120, 70, True),  # Both ten-minute buffers fit exactly.
        (60, 120, 69, False),
        (-30, 120, 5, False),  # Preparation would begin before the actual check.
    ],
)
async def test_new_event_owns_both_buffers(monkeypatch, left, right, offset, allowed):
    user_id, workspace_id = uuid4(), uuid4()
    first, last = NOW - timedelta(hours=1), NOW + timedelta(hours=3)
    snapshot = AvailabilitySnapshot(
        input=AvailabilityRequest(
            user_id=user_id,
            workspace_id=workspace_id,
            start_at=first,
            end_at=last,
            timezone="UTC",
            now=NOW,
            preferences=Preferences(),
            routine_confirmed=True,
            required_source_ids=("fixture",),
            coverage=(
                SourceCoverage(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source_id="fixture",
                    state="complete",
                    start_at=first,
                    end_at=last,
                    observed_at=NOW,
                    fresh_until=NOW + timedelta(minutes=5),
                    revision="fixture-v1",
                ),
            ),
            duration_minutes=30,
        ),
        checked_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
        revision=AvailabilityRevisions(
            student_revision="a" * 64,
            planning_revision=0,
            preferences_revision=0,
            availability_generation=0,
            availability_revision=0,
        ),
        source_summary=(
            AvailabilitySourceSummary(source_id="fixture", state="complete", calendar_count=0),
        ),
        result=AvailabilityResult(
            timezone="UTC",
            checked_at=NOW,
            status="complete",
            confirmed_openings=(
                Opening(
                    start_at=NOW + timedelta(minutes=left),
                    end_at=NOW + timedelta(minutes=right),
                    minutes=right - left,
                ),
            ),
            provisional_openings=(),
            busy=(),
            warnings=(),
        ),
    )

    async def preview(*args):
        return snapshot

    monkeypatch.setattr(scheduling.availability, "preview", preview)
    draft = CalendarEventPreview(
        generation=0,
        revision=0,
        request_id=uuid4(),
        grant_id=uuid4(),
        calendar_id="fixture",
        title="Meeting",
        start_at=NOW + timedelta(minutes=offset),
        end_at=NOW + timedelta(minutes=offset + 30),
        timezone="UTC",
        availability=AvailabilityPreview(
            start_at=first,
            end_at=last,
            duration_minutes=30,
            preferences_revision=0,
            expected_timezone="UTC",
            routine_confirmed=True,
            travel_assumption="zero",
        ),
    )
    assert not draft.allow_study_displacement and not snapshot.input.study_blocks
    if allowed:
        result = await scheduling._free(None, None, user_id, workspace_id, "synthetic", draft)
        assert result is snapshot
    else:
        with pytest.raises(StudentError, match="no longer confirmed free"):
            await scheduling._free(None, None, user_id, workspace_id, "synthetic", draft)
