from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from navox.student_availability.engine import compute_availability
from navox.student_availability.schemas import (
    AvailabilityRequest,
    BusyInterval,
    SourceCoverage,
    StudyBlock,
    TimeInterval,
)
from navox.student_availability.selector import Crunch, SuggestionRequest, suggest
from navox.student_planning.schemas import Preferences

OWNER = {
    "user_id": UUID("10000000-0000-0000-0000-000000000001"),
    "workspace_id": UUID("20000000-0000-0000-0000-000000000001"),
}
DAY = datetime(2026, 10, 22, tzinfo=UTC)


def at(hour, minute=0):
    return DAY + timedelta(hours=hour, minutes=minute)


def busy(identifier, first, last, kind="class"):
    return BusyInterval.model_validate(
        {
            **OWNER,
            "id": identifier,
            "source_id": "schedule",
            "kind": kind,
            "start_at": first,
            "end_at": last,
            "travel_before_minutes": 0,
            "travel_after_minutes": 0,
        }
    )


def availability(*, records=None, studies=(), stale=False):
    if records is None:
        records = (
            busy("math", at(9, 5), at(9, 55)),
            busy("ece", at(12, 30), at(13, 45)),
            busy("shift", at(17), at(20), kind="shift"),
        )
    coverage = SourceCoverage.model_validate(
        {
            **OWNER,
            "source_id": "schedule",
            "state": "complete",
            "start_at": DAY - timedelta(days=1),
            "end_at": DAY + timedelta(days=2),
            "observed_at": at(6, 59),
            "fresh_until": at(7) if stale else at(8),
            "revision": "r1",
        }
    )
    request = AvailabilityRequest.model_validate(
        {
            **OWNER,
            "start_at": DAY,
            "end_at": DAY + timedelta(days=1),
            "now": at(7),
            "timezone": "UTC",
            "preferences": Preferences(wake_time="08:00", sleep_time="23:00"),
            "routine_confirmed": True,
            "required_source_ids": ["schedule"],
            "coverage": [coverage],
            "busy": list(records),
            "study_blocks": list(studies),
        }
    )
    return compute_availability(request)


def ask(purpose, result=None, **changes):
    return suggest(
        SuggestionRequest(
            availability=result if result is not None else availability(),
            now=at(7),
            purpose=purpose,
            **changes,
        )
    )


def test_coffee_offers_soonest_and_a_spread_out_second_option():
    picked = ask("coffee")
    assert picked.status == "ok"
    first, second = picked.suggestions
    assert (first.start_at, first.end_at) == (at(8), at(8, 30))
    assert "soonest" in first.reasons
    # Second option is at least 90 minutes away and follows the 9:05 class plus buffer.
    assert second.start_at == at(10, 5)
    assert "right_after_commitment" in second.reasons
    assert picked.changes_applied is False


def test_office_hours_stay_inside_the_professors_hours():
    picked = ask(
        "office_hours",
        other_windows=(TimeInterval(start_at=at(15), end_at=at(16)),),
        require_other_windows=True,
    )
    assert [(item.start_at, item.end_at) for item in picked.suggestions] == [(at(15), at(15, 30))]
    assert "within_their_hours" in picked.suggestions[0].reasons


def test_preferred_other_hours_rank_first_without_being_required():
    picked = ask("coffee", other_windows=(TimeInterval(start_at=at(14), end_at=at(15)),))
    assert picked.suggestions[0].start_at == at(14)
    assert "within_their_hours" in picked.suggestions[0].reasons


def test_lunch_only_inside_lunch_hours_and_honest_when_none_fit():
    # The 12:30 class plus buffers leaves no 60 minutes inside 11:30-14:00.
    assert ask("lunch").status == "no_time"
    open_noon = availability(records=(busy("math", at(9, 5), at(9, 55)),))
    picked = ask("lunch", open_noon)
    assert (picked.suggestions[0].start_at, picked.suggestions[0].end_at) == (at(12), at(13))
    assert "lunch_time" in picked.suggestions[0].reasons
    for item in picked.suggestions:
        assert at(11, 30) <= item.start_at and item.end_at <= at(14)


def test_nothing_stacked_right_before_an_exam():
    exam = Crunch(at=at(11), kind="exam")
    picked = ask("coffee", crunch=(exam,))
    assert picked.suggestions
    for item in picked.suggestions:
        assert not (exam.at - timedelta(minutes=60) < item.end_at <= exam.at)
        assert not (item.start_at < exam.at < item.end_at)


def test_unchecked_calendar_time_is_never_offered_as_free():
    picked = ask("coffee", availability(stale=True))
    assert picked.status == "no_time"
    assert picked.suggestions == ()


def test_moving_study_work_is_disclosed():
    block = StudyBlock.model_validate(
        {**OWNER, "id": "study-lab", "start_at": at(14), "end_at": at(15), "movable": True}
    )
    picked = ask(
        "project_meeting",
        availability(studies=(block,)),
        other_windows=(TimeInterval(start_at=at(14), end_at=at(15)),),
        require_other_windows=True,
    )
    (only,) = picked.suggestions
    assert (only.start_at, only.end_at) == (at(14), at(15))
    assert "moves_study_block" in only.reasons
    assert only.displaced_study_block_ids == ("study-lab",)


def test_unclear_purpose_asks_for_a_duration():
    assert ask("other").status == "needs_duration"
    assert ask("other", duration_minutes=45).suggestions[0].minutes == 45


def test_options_never_overlap_and_fit_their_openings():
    result = availability()
    picked = ask("project_meeting", result, count=3)
    spans = [(item.start_at, item.end_at) for item in picked.suggestions]
    for index, (first, last) in enumerate(spans):
        assert last - first == timedelta(minutes=60)
        assert any(
            opening.start_at <= first and last <= opening.end_at
            for opening in result.confirmed_openings
        )
        for other in spans[index + 1 :]:
            assert last <= other[0] or other[1] <= first


def test_times_align_to_local_half_hours_in_the_students_zone():
    local = availability()
    shifted = local.model_copy(update={"timezone": "America/New_York"})
    edges = {opening.start_at for opening in local.confirmed_openings}
    zone = ZoneInfo("America/New_York")
    for item in ask("coffee", shifted).suggestions:
        assert item.start_at in edges or item.start_at.astimezone(zone).minute in (0, 30)
