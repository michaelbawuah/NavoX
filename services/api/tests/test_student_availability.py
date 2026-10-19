from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from navox.student_availability.engine import compute_availability
from navox.student_availability.schemas import (
    AvailabilityRequest,
    BusyInterval,
    ProjectionUncertainty,
    SourceCoverage,
    StudyBlock,
)
from navox.student_planning.schemas import DailyWindow, Preferences

OWNER = {
    "user_id": UUID("10000000-0000-0000-0000-000000000001"),
    "workspace_id": UUID("20000000-0000-0000-0000-000000000001"),
}
DAY = datetime(2026, 10, 19, tzinfo=UTC)


def at(hour, minute=0, *, day=0):
    return DAY + timedelta(days=day, hours=hour, minutes=minute)


def coverage(source="schedule", **changes):
    return SourceCoverage.model_validate(
        {
            **OWNER,
            "source_id": source,
            "state": "complete",
            "start_at": DAY,
            "end_at": DAY + timedelta(days=1),
            "observed_at": at(7, 59),
            "fresh_until": at(8, 5),
            "revision": "revision-1",
            **changes,
        }
    )


def busy(first, last, **changes):
    return BusyInterval.model_validate(
        {
            **OWNER,
            "id": "busy-1",
            "source_id": "schedule",
            "kind": "fixed",
            "start_at": first,
            "end_at": last,
            **changes,
        }
    )


def study(first, last, **changes):
    return StudyBlock.model_validate(
        {
            **OWNER,
            "id": "study-1",
            "start_at": first,
            "end_at": last,
            **changes,
        }
    )


def request(**changes):
    return AvailabilityRequest.model_validate(
        {
            **OWNER,
            "start_at": DAY,
            "end_at": DAY + timedelta(days=1),
            "now": at(8),
            "timezone": "UTC",
            "preferences": Preferences(wake_time="09:00", sleep_time="17:00"),
            "routine_confirmed": True,
            "required_source_ids": ["schedule"],
            "coverage": [coverage()],
            **changes,
        }
    )


def spans(openings):
    return [(item.start_at, item.end_at) for item in openings]


def codes(result):
    return {item.code for item in result.warnings}


def test_complete_sources_produce_only_provided_scope_waking_openings_without_any_write():
    result = compute_availability(request())
    assert result.status == "complete"
    assert spans(result.confirmed_openings) == [(at(9), at(17))]
    assert result.confirmed_openings[0].minutes == 480
    assert result.provisional_openings == ()
    assert result.changes_applied is False
    assert result.scope == "provided_owned_sources_only"
    assert result.warnings == ()


def test_known_walking_time_and_ten_minute_meeting_buffers_are_additive():
    result = compute_availability(
        request(
            busy=[
                busy(
                    at(10),
                    at(11),
                    kind="class",
                    travel_before_minutes=15,
                    travel_after_minutes=5,
                )
            ]
        )
    )
    assert spans(result.busy) == [(at(9, 35), at(11, 15))]
    assert spans(result.confirmed_openings) == [(at(9), at(9, 35)), (at(11, 15), at(17))]


def test_fixed_shifts_meals_and_explicit_travel_remain_hard_blockers_without_meeting_padding():
    result = compute_availability(
        request(
            busy=[
                busy(at(10), at(11), id="meal", kind="fixed"),
                busy(at(11), at(12), id="shift", kind="shift"),
                busy(at(12), at(12, 30), id="walk", kind="travel"),
            ]
        )
    )
    assert spans(result.busy) == [(at(10), at(12, 30))]
    assert spans(result.confirmed_openings) == [(at(9), at(10)), (at(12, 30), at(17))]


def test_overlapping_nested_and_touching_intervals_merge_deterministically():
    records = [
        busy(at(10), at(12), id="one"),
        busy(at(11), at(13), id="two"),
        busy(at(11, 30), at(11, 45), id="nested"),
        busy(at(13), at(14), id="touching"),
    ]
    first = compute_availability(request(busy=records))
    second = compute_availability(request(busy=list(reversed(records))))
    assert first == second
    assert spans(first.busy) == [(at(10), at(14))]
    assert spans(first.confirmed_openings) == [(at(9), at(10)), (at(14), at(17))]


@pytest.mark.parametrize("state", ["partial", "stale", "unavailable", "revoked"])
def test_incomplete_source_state_never_turns_its_unchecked_gaps_into_confirmed_free(state):
    result = compute_availability(request(coverage=[coverage(state=state)]))
    assert result.confirmed_openings == ()
    assert spans(result.provisional_openings) == [(at(9), at(17))]
    assert result.provisional_openings[0].unchecked_source_ids == ("schedule",)
    assert result.provisional_openings[0].reasons == ("coverage_unknown",)
    assert f"coverage_{state}" in codes(result)
    assert result.status == "partial"


def test_missing_connected_calendar_is_explicit_even_when_other_calendar_has_no_events():
    result = compute_availability(request(required_source_ids=["personal", "schedule"]))
    assert result.confirmed_openings == ()
    assert "coverage_missing" in codes(result)
    assert result.provisional_openings[0].unchecked_source_ids == ("personal",)


def test_stale_sources_still_supply_conservative_busy_blockers():
    result = compute_availability(
        request(coverage=[coverage(state="partial")], busy=[busy(at(10), at(11))])
    )
    assert result.confirmed_openings == ()
    assert spans(result.provisional_openings) == [(at(9), at(10)), (at(11), at(17))]


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"fresh_until": at(8)}, "coverage_stale"),
        ({"observed_at": at(8, 1)}, "coverage_future"),
    ],
)
def test_freshness_includes_exact_expiry_and_impossible_future_observation(changes, code):
    result = compute_availability(request(coverage=[coverage(**changes)]))
    assert result.confirmed_openings == ()
    assert code in codes(result)


def test_coverage_range_does_not_certify_the_unchecked_part_of_an_opening():
    result = compute_availability(request(coverage=[coverage(start_at=at(12))]))
    assert result.confirmed_openings == ()
    assert spans(result.provisional_openings) == [(at(9), at(17))]
    assert "coverage_outside_window" in codes(result)


def test_projection_warnings_preserve_partial_day_unknown_evidence_without_losing_known_blockers():
    issue = ProjectionUncertainty(
        **OWNER,
        source_id="schedule",
        start_at=at(12),
        end_at=at(13),
        reason="projection_warning",
    )
    result = compute_availability(request(busy=[busy(at(10), at(11))], uncertainties=[issue]))
    assert spans(result.confirmed_openings) == [(at(9), at(10))]
    assert spans(result.provisional_openings) == [(at(11), at(17))]
    assert "projection_uncertainty" in codes(result)


def test_unconfirmed_default_routine_is_only_an_assumed_candidate():
    result = compute_availability(request(routine_confirmed=False))
    assert result.status == "needs_routine"
    assert result.confirmed_openings == ()
    assert result.provisional_openings[0].reasons == ("routine_unconfirmed",)
    assert "routine_unconfirmed" in codes(result)


def test_missing_travel_is_not_silently_assumed_zero_or_called_suitable():
    result = compute_availability(request(busy=[busy(at(10), at(11), kind="meeting")]))
    assert result.confirmed_openings == ()
    assert spans(result.provisional_openings) == [(at(9), at(9, 50)), (at(11, 10), at(17))]
    assert all(
        item.unknown_travel_record_ids == ("busy-1",) for item in result.provisional_openings
    )
    assert "travel_unknown" in codes(result)


@pytest.mark.parametrize(
    "first,last,day",
    [(at(0, 5, day=1), at(1, day=1), 0), (at(23, day=0), at(23, 55, day=0), 1)],
)
def test_unknown_travel_across_midnight_never_makes_the_adjacent_day_confirmed(first, last, day):
    start, end = DAY + timedelta(days=day), DAY + timedelta(days=day + 1)
    now = start + timedelta(hours=20) if day == 0 else start
    result = compute_availability(
        request(
            start_at=start,
            end_at=end,
            now=now,
            preferences=Preferences(wake_time="00:01", sleep_time="23:59"),
            coverage=[
                coverage(
                    start_at=start,
                    end_at=end,
                    observed_at=now - timedelta(minutes=1),
                    fresh_until=now + timedelta(minutes=5),
                )
            ],
            busy=[busy(first, last, kind="meeting")],
        )
    )
    assert result.confirmed_openings == ()
    assert result.provisional_openings
    assert "travel_unknown" in codes(result)


def test_missing_travel_on_one_day_does_not_poison_an_unrelated_later_day():
    end = DAY + timedelta(days=3)
    result = compute_availability(
        request(
            end_at=end,
            coverage=[coverage(end_at=end)],
            busy=[busy(at(10), at(11), kind="class")],
        )
    )
    assert spans(result.confirmed_openings) == [
        (at(9, day=1), at(17, day=1)),
        (at(9, day=2), at(17, day=2)),
    ]


def test_explicit_zero_travel_for_a_remote_meeting_is_known():
    result = compute_availability(
        request(
            busy=[
                busy(
                    at(10), at(11), kind="meeting", travel_before_minutes=0, travel_after_minutes=0
                )
            ]
        )
    )
    assert result.confirmed_openings
    assert result.status == "complete"
    assert "travel_unknown" not in codes(result)


def test_movable_internal_work_is_disclosed_without_claiming_a_replacement_plan():
    source = request(study_blocks=[study(at(12), at(13), movable=True)])
    before = source.model_dump(mode="json")
    result = compute_availability(source)
    assert source.model_dump(mode="json") == before
    assert result.confirmed_openings == ()
    assert spans(result.provisional_openings) == [(at(9), at(17))]
    assert result.provisional_openings[0].displaced_study_block_ids == ("study-1",)
    assert result.provisional_openings[0].reasons == ("movable_study",)
    assert result.changes_applied is False


@pytest.mark.parametrize(
    "changes",
    [{}, {"movable": True, "calendar_backed": True}, {"movable": True, "needs_review": True}],
)
def test_fixed_calendar_backed_or_stale_study_work_is_not_treated_as_movable(changes):
    result = compute_availability(request(study_blocks=[study(at(12), at(13), **changes)]))
    assert spans(result.confirmed_openings) == [(at(9), at(12)), (at(13), at(17))]
    assert result.provisional_openings == ()
    assert "movable_study" not in codes(result)


def test_a_started_study_block_stays_fixed_even_if_its_old_definition_says_movable():
    result = compute_availability(
        request(
            now=at(10, 15),
            coverage=[coverage(observed_at=at(10, 14), fresh_until=at(10, 20))],
            study_blocks=[study(at(10), at(11), movable=True)],
        )
    )
    assert spans(result.confirmed_openings) == [(at(11), at(17))]
    assert result.provisional_openings == ()


def test_never_schedule_is_hard_while_focus_hours_are_only_preferences():
    prefs = Preferences(
        wake_time="09:00",
        sleep_time="17:00",
        never_schedule=[DailyWindow(start_time="11:00", end_time="12:00")],
        focus_windows=[DailyWindow(start_time="13:00", end_time="14:00")],
    )
    before = prefs.model_dump(mode="json")
    result = compute_availability(request(preferences=prefs))
    assert spans(result.confirmed_openings) == [(at(9), at(11)), (at(12), at(17))]
    assert prefs.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "options,expected",
    [
        ({}, []),
        ({"duration_minutes": 15}, [(at(9), at(9, 15))]),
        ({"include_short_slots": True}, [(at(9), at(9, 15))]),
        ({"duration_minutes": 20}, []),
    ],
)
def test_explicit_short_meetings_fit_only_after_the_full_meeting_buffer(options, expected):
    result = compute_availability(
        request(
            busy=[
                busy(
                    at(9, 25),
                    at(17),
                    kind="meeting",
                    travel_before_minutes=0,
                    travel_after_minutes=0,
                )
            ],
            **options,
        )
    )
    assert spans(result.confirmed_openings) == expected


def test_overnight_waking_routine_does_not_offer_sleep_as_free():
    result = compute_availability(
        request(
            now=DAY,
            coverage=[coverage(observed_at=DAY - timedelta(minutes=1), fresh_until=at(1))],
            preferences=Preferences(wake_time="16:00", sleep_time="06:00"),
        )
    )
    assert spans(result.confirmed_openings) == [(at(0), at(6)), (at(16), at(0, day=1))]


@pytest.mark.parametrize(
    "day,wake,sleep,expected",
    [
        (datetime(2027, 3, 14, 5, tzinfo=UTC), "01:00", "04:00", 120),
        (datetime(2026, 11, 1, 4, tzinfo=UTC), "00:00", "03:00", 240),
    ],
)
def test_dst_uses_real_elapsed_minutes_with_unambiguous_wall_clock_boundaries(
    day, wake, sleep, expected
):
    end = day + timedelta(hours=12)
    result = compute_availability(
        request(
            start_at=day,
            end_at=end,
            now=day,
            timezone="America/New_York",
            preferences=Preferences(wake_time=wake, sleep_time=sleep),
            coverage=[
                coverage(
                    start_at=day,
                    end_at=end,
                    observed_at=day - timedelta(minutes=1),
                    fresh_until=day + timedelta(minutes=5),
                )
            ],
        )
    )
    assert sum(item.minutes for item in result.confirmed_openings) == expected


@pytest.mark.parametrize(
    "day,wake",
    [
        (datetime(2027, 3, 14, 5, tzinfo=UTC), "02:30"),
        (datetime(2026, 11, 1, 4, tzinfo=UTC), "01:30"),
    ],
)
def test_ambiguous_or_nonexistent_wake_time_is_withheld_instead_of_guessed(day, wake):
    end = day + timedelta(hours=12)
    result = compute_availability(
        request(
            start_at=day,
            end_at=end,
            now=day,
            timezone="America/New_York",
            preferences=Preferences(wake_time=wake, sleep_time="08:00"),
            coverage=[
                coverage(
                    start_at=day,
                    end_at=end,
                    observed_at=day - timedelta(minutes=1),
                    fresh_until=day + timedelta(minutes=5),
                )
            ],
        )
    )
    assert result.confirmed_openings == result.provisional_openings == ()
    assert "routine_clock_change" in codes(result)
    assert result.status == "partial"


def test_ambiguous_never_schedule_window_is_kept_unavailable():
    day = datetime(2026, 11, 1, 4, tzinfo=UTC)
    end = day + timedelta(hours=12)
    result = compute_availability(
        request(
            start_at=day,
            end_at=end,
            now=day,
            timezone="America/New_York",
            preferences=Preferences(
                wake_time="00:00",
                sleep_time="08:00",
                never_schedule=[DailyWindow(weekdays=[6], start_time="01:15", end_time="01:45")],
            ),
            coverage=[
                coverage(
                    start_at=day,
                    end_at=end,
                    observed_at=day - timedelta(minutes=1),
                    fresh_until=day + timedelta(minutes=5),
                )
            ],
        )
    )
    assert result.confirmed_openings == result.provisional_openings == ()
    assert "never_schedule_clock_change" in codes(result)


def test_all_day_intervals_are_already_resolved_and_use_exclusive_end_without_extra_padding():
    end = DAY + timedelta(days=2)
    result = compute_availability(
        request(
            end_at=end,
            coverage=[coverage(end_at=end)],
            busy=[busy(DAY, DAY + timedelta(days=1), kind="all_day")],
        )
    )
    assert spans(result.confirmed_openings) == [(at(9, day=1), at(17, day=1))]
    assert spans(result.busy) == [(DAY + timedelta(hours=8), DAY + timedelta(days=1))]


def test_queries_clip_past_time_and_never_return_zero_or_negative_openings():
    result = compute_availability(
        request(now=at(12), coverage=[coverage(observed_at=at(11, 59), fresh_until=at(12, 5))])
    )
    assert spans(result.confirmed_openings) == [(at(12), at(17))]
    past = compute_availability(request(now=at(0, day=1)))
    assert past.confirmed_openings == past.provisional_openings == ()


@pytest.mark.parametrize("days", [1, 7, 14])
def test_local_day_horizon_is_bounded_and_accepts_fourteen_day_dst_variations(days):
    start = datetime(2026, 10, 25, 4, tzinfo=UTC)
    # New York midnight after the fall clock change is one UTC hour later.
    end = start + timedelta(days=days, hours=1 if days >= 8 else 0)
    source = request(
        start_at=start,
        end_at=end,
        now=start,
        timezone="America/New_York",
        coverage=[
            coverage(
                start_at=start,
                end_at=end,
                observed_at=start - timedelta(minutes=1),
                fresh_until=start + timedelta(minutes=5),
            )
        ],
    )
    assert compute_availability(source).confirmed_openings


@pytest.mark.parametrize(
    "changes",
    [
        {"end_at": DAY + timedelta(days=15)},
        {"start_at": DAY.replace(tzinfo=None)},
        {"now": at(8).replace(tzinfo=None)},
        {"start_at": datetime(1, 1, 1, tzinfo=UTC)},
        {"end_at": datetime(9999, 12, 31, tzinfo=UTC)},
        {"end_at": DAY},
        {"timezone": "Not/A_Timezone"},
        {"required_source_ids": []},
        {"required_source_ids": ["schedule", "schedule"]},
        {"required_source_ids": ["bad source"]},
        {"required_source_ids": ["bad\x7fsource"]},
        {"duration_minutes": 0},
        {"duration_minutes": True},
        {"routine_confirmed": "true"},
    ],
)
def test_invalid_or_overbudget_query_contract_is_rejected_before_computation(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize("kind", ["busy", "coverage", "study_blocks", "uncertainties"])
def test_facts_from_another_user_or_workspace_are_not_silently_combined(kind):
    wrong_owner = {**OWNER, "workspace_id": UUID("30000000-0000-0000-0000-000000000001")}
    facts = {
        "busy": busy(at(10), at(11), **wrong_owner),
        "coverage": coverage(**wrong_owner),
        "study_blocks": study(at(10), at(11), **wrong_owner),
        "uncertainties": ProjectionUncertainty(
            **wrong_owner,
            source_id="schedule",
            start_at=at(10),
            end_at=at(11),
            reason="unresolved_time",
        ),
    }
    with pytest.raises(ValidationError, match="owner scope"):
        request(**{kind: [facts[kind]]})


@pytest.mark.parametrize(
    "identifier", [" ", "hidden\x00id", "hidden\x7fid", "x" * 129, "two words"]
)
def test_source_and_fact_identifiers_are_bounded_and_safe_for_content_free_diagnostics(identifier):
    with pytest.raises(ValidationError):
        busy(at(10), at(11), id=identifier)
    with pytest.raises(ValidationError):
        coverage(source=identifier)


def test_duplicate_unknown_source_and_excessive_record_inputs_are_rejected():
    invalid = [
        {"busy": [busy(at(10), at(11)), busy(at(12), at(13))]},
        {"coverage": [coverage(), coverage()]},
        {"busy": [busy(at(10), at(11), source_id="unlisted")]},
        {"busy": [busy(at(10), at(11))] * 10_001},
        {"study_blocks": [study(at(10), at(11))] * 601},
    ]
    for changes in invalid:
        with pytest.raises(ValidationError):
            request(**changes)


def test_private_titles_provider_payloads_and_all_day_travel_are_outside_the_contract():
    with pytest.raises(ValidationError):
        busy(at(10), at(11), title="Private appointment title")
    with pytest.raises(ValidationError):
        busy(at(10), at(11), kind="all_day", travel_before_minutes=15)


def test_varied_owned_snapshots_have_sorted_disjoint_openings_and_preserve_all_hard_constraints():
    for count in range(1, 35):
        records = [
            busy(
                at(9) + timedelta(minutes=index * 13),
                at(9) + timedelta(minutes=index * 13 + 7),
                id=f"record-{index}",
            )
            for index in range(count)
        ]
        source = request(busy=records, duration_minutes=1)
        before = source.model_dump(mode="json")
        result = compute_availability(source)
        assert source.model_dump(mode="json") == before
        assert result == compute_availability(source)
        openings = sorted(
            (*result.confirmed_openings, *result.provisional_openings),
            key=lambda item: item.start_at,
        )
        for opening in openings:
            assert at(9) <= opening.start_at < opening.end_at <= at(17)
            assert opening.start_at >= source.now
            assert all(
                not (opening.start_at < record.end_at and record.start_at < opening.end_at)
                for record in records
            )
        assert all(
            left.end_at <= right.start_at
            for left, right in zip(openings, openings[1:], strict=False)
        )


def test_diagnostic_associations_have_a_total_budget_instead_of_multiplying_without_bound():
    records = [
        busy(
            at(9) + timedelta(minutes=index * 2),
            at(9) + timedelta(minutes=index * 2 + 1),
            id=f"record-{index}",
        )
        for index in range(150)
    ]
    source = request(
        required_source_ids=["schedule", *(f"calendar-{index}" for index in range(199))],
        busy=records,
        duration_minutes=1,
    )
    with pytest.raises(ValueError, match="diagnostic references; narrow"):
        compute_availability(source)


def test_one_movable_study_warning_is_emitted_once_across_many_separate_candidate_gaps():
    result = compute_availability(
        request(
            study_blocks=[study(at(9), at(17), movable=True)],
            busy=[busy(at(10), at(11)), busy(at(12), at(13), id="busy-2")],
        )
    )
    assert len(result.provisional_openings) == 3
    assert sum(warning.code == "movable_study" for warning in result.warnings) == 1
