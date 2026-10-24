from datetime import UTC, date, datetime, timedelta

from navox.student_planning.planner import available_slots, build_plan
from navox.student_planning.schemas import Preferences


def test_overlapping_daily_wakes_count_each_minute_once():
    preferences = Preferences(
        wake_times=["22:00", "20:00", "07:00", "07:00", "07:00", "07:00", "07:00"],
        sleep_time="21:00",
    )
    start = datetime(2026, 10, 26, tzinfo=UTC)
    end = start + timedelta(days=2)
    slots, warnings = available_slots(preferences, "UTC", start_at=start, end_at=end, busy=[])
    assert not warnings
    assert all(first.end <= second.start for first, second in zip(slots, slots[1:], strict=False))
    result = build_plan([], preferences, "UTC", start_at=start, end_at=end, busy=[])
    assert [(item.day, item.free_minutes, item.buffer_minutes) for item in result.capacities] == [
        (date(2026, 10, 26), 120, 18),
        (date(2026, 10, 27), 1260, 189),
    ]


def test_smart_wake_on_previous_evening_preserves_unique_capacity_and_busy_time():
    preferences = Preferences()
    start = datetime(2026, 10, 26, tzinfo=UTC)
    end = start + timedelta(days=2)
    # A just-after-midnight class with preparation/travel can move Tuesday's
    # smart alarm into Monday evening, inside Monday's existing awake interval.
    overrides = {date(2026, 10, 27): start + timedelta(hours=22, minutes=40)}
    meeting_start = start + timedelta(hours=22, minutes=45)
    meeting_end = start + timedelta(hours=23, minutes=15)
    result = build_plan(
        [],
        preferences,
        "UTC",
        start_at=start,
        end_at=end,
        busy=[(meeting_start, meeting_end)],
        wake_overrides=overrides,
    )
    assert [(item.day, item.free_minutes) for item in result.capacities] == [
        (date(2026, 10, 26), 990),
        (date(2026, 10, 27), 1380),
    ]
    slots, _ = available_slots(
        preferences,
        "UTC",
        start_at=start,
        end_at=end,
        busy=[(meeting_start, meeting_end)],
        wake_overrides=overrides,
    )
    assert all(first.end <= second.start for first, second in zip(slots, slots[1:], strict=False))
    assert all(slot.end <= meeting_start or slot.start >= meeting_end for slot in slots)
    assert all(slot.start.date() == slot.day for slot in slots)
