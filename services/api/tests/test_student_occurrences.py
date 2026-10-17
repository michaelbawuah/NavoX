from datetime import UTC, date, datetime
from uuid import uuid4

from navox.student import occurrences
from navox.student.schemas import CourseView, MeetingView


def test_maximum_sections_and_month_have_bounded_conflict_construction(monkeypatch):
    course_id = uuid4()
    sections = [
        MeetingView(
            id=uuid4(),
            course_id=course_id,
            kind="lecture",
            weekdays=list(range(7)),
            start_time="10:00",
            end_time="11:00",
            timezone="UTC",
            building=None,
            room=None,
            instructor=None,
            walk_minutes=None,
            dates=None,
            revision=1,
        )
        for _ in range(200)
    ]
    course = CourseView(
        id=course_id,
        code="TEST",
        name="Reviewed test sections",
        term_start=date(2026, 10, 1),
        term_end=date(2026, 10, 31),
        professor=None,
        archived=False,
        revision=1,
        meetings=sections,
    )
    original = occurrences.Conflict
    constructed = 0

    def bounded_conflict(**kwargs):
        nonlocal constructed
        constructed += 1
        assert constructed <= occurrences.MAX_CONFLICTS
        return original(**kwargs)

    monkeypatch.setattr(occurrences, "Conflict", bounded_conflict)
    args = dict(
        start_at=datetime(2026, 10, 1, tzinfo=UTC), end_at=datetime(2026, 11, 1, tzinfo=UTC)
    )
    issues = []
    rows, fixed, conflicts, warnings = occurrences.project(
        [course], [], [], [], class_issues=issues, **args
    )
    assert len(rows) == 6_200 and fixed == []
    assert len(conflicts) == constructed == 200
    assert occurrences.TRUNCATED_CONFLICTS_WARNING in warnings
    assert issues == []  # Conflict truncation does not make class times incomplete.
    # Alerts and future-search paths do not construct even one conflict.
    constructed = 0
    rows, _, conflicts, warnings = occurrences.project(
        [course], [], [], [], include_conflicts=False, **args
    )
    assert len(rows) == 6_200 and conflicts == [] and constructed == 0
    assert occurrences.TRUNCATED_CONFLICTS_WARNING not in warnings
