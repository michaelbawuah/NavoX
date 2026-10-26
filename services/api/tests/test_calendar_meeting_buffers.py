"""Exact-write approval cannot bypass the new meeting's own preparation buffer."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from navox.student.service import StudentError
from navox.student_calendars import scheduling

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
    snapshot = SimpleNamespace(
        checked_at=NOW,
        result=SimpleNamespace(
            status="complete",
            confirmed_openings=[
                SimpleNamespace(
                    start_at=NOW + timedelta(minutes=left),
                    end_at=NOW + timedelta(minutes=right),
                    displaced_study_block_ids=[],
                )
            ],
        ),
    )

    async def preview(*args):
        return snapshot

    monkeypatch.setattr(scheduling.availability, "preview", preview)
    draft = SimpleNamespace(
        start_at=NOW + timedelta(minutes=offset),
        end_at=NOW + timedelta(minutes=offset + 30),
        availability=object(),
    )
    if allowed:
        assert await scheduling._free(None, None, uuid4(), uuid4(), "synthetic", draft) is snapshot
    else:
        with pytest.raises(StudentError, match="no longer confirmed free"):
            await scheduling._free(None, None, uuid4(), uuid4(), "synthetic", draft)
