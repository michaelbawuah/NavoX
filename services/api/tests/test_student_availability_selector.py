from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

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
    assert (first.start_at, first.end_at) == (at(8, 10), at(8, 40))
    assert "soonest" in first.reasons
    # Existing class padding and the new meeting buffer both remain protected.
    assert second.start_at == at(10, 15)
    assert "right_after_commitment" in second.reasons
    assert picked.changes_applied is False


def test_office_hours_stay_inside_the_professors_hours():
    picked = ask(
        "office_hours",
        other_windows=(TimeInterval(start_at=at(15), end_at=at(16)),),
        require_other_windows=True,
    )
    assert [(item.start_at, item.end_at) for item in picked.suggestions] == [
        (at(15), at(15, 30)),
        (at(15, 30), at(16)),
    ]
    assert "within_their_hours" in picked.suggestions[0].reasons


def test_preferred_other_hours_rank_first_without_being_required():
    picked = ask("coffee", other_windows=(TimeInterval(start_at=at(14), end_at=at(15)),))
    assert picked.suggestions[0].start_at == at(14, 5)
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
    assert picked.status == "partial_information"
    assert picked.suggestions == ()


def test_moving_study_work_requires_a_feasible_replacement_before_offer():
    block = StudyBlock.model_validate(
        {**OWNER, "id": "study-lab", "start_at": at(14), "end_at": at(15), "movable": True}
    )
    picked = ask(
        "project_meeting",
        availability(studies=(block,)),
        other_windows=(TimeInterval(start_at=at(14), end_at=at(15)),),
        require_other_windows=True,
    )
    assert picked.suggestions == ()
    assert picked.status == "partial_information"


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
    edges = {
        edge
        for opening in local.confirmed_openings
        for edge in (
            opening.start_at + timedelta(minutes=10),
            opening.end_at - timedelta(minutes=40),
        )
    }
    zone = ZoneInfo("America/New_York")
    for item in ask("coffee", shifted).suggestions:
        assert item.start_at in edges or item.start_at.astimezone(zone).minute in (0, 30)


def test_nonaligned_other_person_window_is_not_missed():
    picked = ask(
        "office_hours",
        availability(records=()),
        duration_minutes=15,
        other_windows=(TimeInterval(start_at=at(14, 10), end_at=at(14, 25)),),
        require_other_windows=True,
    )
    assert [(item.start_at, item.end_at) for item in picked.suggestions] == [
        (at(14, 10), at(14, 25))
    ]


def test_new_meeting_buffers_fit_around_shifts_fixed_work_and_routine():
    fixed = StudyBlock.model_validate(
        {**OWNER, "id": "fixed-lab", "start_at": at(14), "end_at": at(15), "movable": False}
    )
    free = availability(records=(busy("job", at(10), at(11), kind="shift"),), studies=(fixed,))
    picked = ask("other", free, duration_minutes=30, count=5)
    assert picked.suggestions
    for item in picked.suggestions:
        assert any(
            opening.start_at <= item.start_at - timedelta(minutes=10)
            and item.end_at + timedelta(minutes=10) <= opening.end_at
            for opening in free.confirmed_openings
        )
        assert item.start_at - timedelta(minutes=10) >= at(7)
        assert not item.displaced_study_block_ids


def test_large_movable_opening_is_withheld_without_false_displacement_claims():
    block = StudyBlock.model_validate(
        {**OWNER, "id": "study-lab", "start_at": at(14), "end_at": at(15), "movable": True}
    )
    free = availability(records=(), studies=(block,))
    # Exact unaffected subintervals need a replacement-aware contract. A coarse
    # provisional opening must not label every morning candidate as displaced.
    picked = ask("coffee", free)
    assert picked.status == "partial_information"
    assert picked.suggestions == ()


@pytest.mark.parametrize(
    "metadata",
    [
        {"reasons": ("coverage_unknown",)},
        {"unchecked_source_ids": ("unread-calendar",)},
        {"unknown_travel_record_ids": ("travel",)},
        {"displaced_study_block_ids": ("study",)},
    ],
)
def test_uncertain_metadata_cannot_be_laundered_through_confirmed_collection(metadata):
    free = availability(records=())
    opening = free.confirmed_openings[0].model_copy(update=metadata)
    free = free.model_copy(update={"confirmed_openings": (opening,)})
    assert ask("coffee", free).suggestions == ()


@pytest.mark.parametrize("age_seconds", [-1, 60, 61])
def test_stale_or_future_observation_requires_recomputation(age_seconds):
    with pytest.raises(ValidationError, match="fresh availability"):
        SuggestionRequest(
            availability=availability(records=()),
            now=at(7) + timedelta(seconds=age_seconds),
            purpose="coffee",
        )


def test_fifty_nine_second_old_observation_is_accepted():
    picked = suggest(
        SuggestionRequest(
            availability=availability(records=()),
            now=at(7) + timedelta(seconds=59),
            purpose="coffee",
        )
    )
    assert picked.suggestions


def test_irrelevant_other_windows_do_not_hide_aligned_candidates():
    windows = tuple(
        TimeInterval(
            start_at=DAY - timedelta(days=2) + timedelta(minutes=index),
            end_at=DAY - timedelta(days=2) + timedelta(minutes=index + 10),
        )
        for index in range(200)
    )
    picked = ask("coffee", availability(records=()), other_windows=windows)
    assert len(picked.suggestions) == 2
    assert picked.suggestions[1].start_at.astimezone(ZoneInfo("UTC")).minute in (0, 30)


def test_fall_back_repeated_hour_uses_distinct_instants_and_elapsed_duration():
    free = availability(records=())
    transition = datetime(2026, 11, 1, 4, tzinfo=UTC)
    opening = free.confirmed_openings[0].model_copy(
        update={"start_at": transition, "end_at": transition + timedelta(hours=4)}
    )
    free = free.model_copy(
        update={
            "timezone": "America/New_York",
            "checked_at": transition,
            "confirmed_openings": (opening,),
        }
    )
    windows = tuple(
        TimeInterval(
            start_at=transition + timedelta(hours=hour, minutes=15),
            end_at=transition + timedelta(hours=hour, minutes=30),
        )
        for hour in (1, 2)
    )
    picked = suggest(
        SuggestionRequest(
            availability=free,
            now=transition,
            purpose="office_hours",
            duration_minutes=15,
            other_windows=windows,
            require_other_windows=True,
        )
    )
    assert [(item.start_at, item.end_at) for item in picked.suggestions] == [
        (window.start_at, window.end_at) for window in windows
    ]
    zone = ZoneInfo("America/New_York")
    assert [item.start_at.astimezone(zone).fold for item in picked.suggestions] == [0, 1]
    assert all(item.end_at - item.start_at == timedelta(minutes=15) for item in picked.suggestions)


def test_buffer_starts_after_now_when_observation_is_inside_an_opening():
    free = availability(records=()).model_copy(update={"checked_at": at(9, 7)})
    picked = suggest(SuggestionRequest(availability=free, now=at(9, 7), purpose="coffee"))
    assert picked.suggestions[0].start_at == at(9, 17)
