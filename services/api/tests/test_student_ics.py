from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_student_timetable import COURSE, register
from test_student_timetable import student_env as _student_env_fixture

from navox.db.student import StudentClassMeeting, StudentImportPreview
from navox.student.ics import TimetableImportError, parse_timetable_ics

student_env = _student_env_fixture


def calendar(*events):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//NavoX tests//EN\r\n"
        + "".join(
            "BEGIN:VEVENT\r\n" + event.replace("\n", "\r\n") + "\r\nEND:VEVENT\r\n"
            for event in events
        )
        + "END:VCALENDAR\r\n"
    )


EVENT = (
    "UID:class-one\nDTSTART;TZID=America/New_York:20261019T100000\n"
    "DTEND;TZID=America/New_York:20261019T110000\n"
    "SUMMARY:CS 2110 Lecture\nLOCATION:Science Hall 101"
)


def parse(content, start="2026-10-01", end="2026-12-01", timezone="America/New_York"):
    return parse_timetable_ics(
        content,
        timezone=timezone,
        term_start=date.fromisoformat(start),
        term_end=date.fromisoformat(end),
    )


def test_ics_weekly_recurrence_exdate_and_override_keep_exact_timing_and_reviewable_groups():
    base = (
        EVENT
        + "\nRRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=5\nEXDATE;TZID=America/New_York:20261021T100000"
    )
    override = (
        "UID:class-one\nRECURRENCE-ID;TZID=America/New_York:20261026T100000\n"
        "DTSTART;TZID=America/New_York:20261026T120000\n"
        "DTEND;TZID=America/New_York:20261026T130000\nLOCATION:Online"
    )
    rows, warnings = parse(calendar(base, override))
    assert sum(len(row.dates) for row in rows) == 4
    changed = next(row for row in rows if row.start_time == "12:00")
    assert changed.dates == [date(2026, 10, 26)] and changed.building == "Online"
    assert all(row.course_code == "" and row.kind == "other" for row in rows)
    assert warnings


def test_ics_duplicate_exact_events_once_and_conflicting_duplicate_refused():
    rows, warnings = parse(calendar(EVENT, EVENT))
    assert len(rows) == 1 and len(rows[0].dates) == 1
    assert any("duplicate" in warning for warning in warnings)
    with pytest.raises(TimetableImportError, match="conflicting duplicate"):
        parse(calendar(EVENT, EVENT.replace("110000", "120000")))


def test_ics_all_day_is_not_invented_class_and_recurrence_cancel_removed():
    all_day = "UID:deadline\nDTSTART;VALUE=DATE:20261019\nSUMMARY:Essay due"
    base = EVENT + "\nRRULE:FREQ=WEEKLY;COUNT=2"
    cancelled = (
        "UID:class-one\nRECURRENCE-ID;TZID=America/New_York:20261026T100000\nSTATUS:CANCELLED"
    )
    rows, warnings = parse(calendar(all_day, base, cancelled))
    assert rows[0].dates == [date(2026, 10, 19)]
    assert any("All-day" in value for value in warnings)


def test_ics_floating_uses_profile_zone_and_utc_remains_utc():
    floating = EVENT.replace(";TZID=America/New_York", "")
    assert parse(calendar(floating), timezone="Europe/London")[0][0].timezone == "Europe/London"
    utc = floating.replace("20261019T100000", "20261019T100000Z").replace(
        "20261019T110000", "20261019T110000Z"
    )
    assert parse(calendar(utc))[0][0].timezone == "UTC"


def test_ics_recurrence_crosses_dst_with_local_time_and_rdate():
    rows, _ = parse(
        calendar(EVENT + "\nRRULE:FREQ=WEEKLY;COUNT=4\nRDATE;TZID=America/New_York:20261022T100000")
    )
    assert rows[0].start_time == "10:00"
    assert date(2026, 11, 2) in rows[0].dates
    assert date(2026, 10, 22) in rows[0].dates


@pytest.mark.parametrize(
    "event,message",
    [
        (EVENT + "\nRRULE:FREQ=MONTHLY;COUNT=3", "daily and weekly"),
        (EVENT + "\nRRULE:FREQ=WEEKLY;BYSETPOS=1;COUNT=3", "not supported"),
        (EVENT.replace("America/New_York", "Custom/School"), "custom time zone"),
        (EVENT.replace("\nDTEND;TZID=America/New_York:20261019T110000", ""), "positive end time"),
        (EVENT.replace("100000", "100001"), "seconds"),
        (EVENT + "\nRRULE:FREQ=WEEKLY;COUNT=3;UNTIL=20261201T000000Z", "COUNT and UNTIL"),
    ],
)
def test_ics_unsupported_or_incomplete_events_fail_actionably(event, message):
    with pytest.raises(TimetableImportError, match=message):
        parse(calendar(event))


def test_ics_one_thousand_events_and_limits():
    content = calendar(
        *(EVENT.replace("UID:class-one", f"UID:event-{index}") for index in range(201))
    )
    with pytest.raises(TimetableImportError, match="200 sections"):
        parse(content)
    with pytest.raises(TimetableImportError, match="2 MB"):
        parse("X" * 2_000_001)
    with pytest.raises(TimetableImportError, match="No timed"):
        parse(calendar(EVENT), start="2027-01-01", end="2027-04-01")


def test_folded_embedded_timezone_is_removed_before_calendar_decoder(monkeypatch):
    from icalendar import Calendar

    original = Calendar.from_ical
    decoded = []

    def inspect(data):
        decoded.append(data)
        assert b"BEGIN:VTIMEZONE" not in data.upper()
        assert b"TZOFFSETFROM" not in data.upper()
        return original(data)

    monkeypatch.setattr(Calendar, "from_ical", staticmethod(inspect))
    zone = (
        "BEGIN:VTIME\r\n ZONE\r\nTZID:Untrusted/Zone\r\n"
        "BEGIN:STANDARD\r\nDTSTART:20260101T000000\r\n"
        "TZOFFSETFROM:+0000\r\nTZOFFSETTO:+0100\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=1\r\nEND:STANDARD\r\n"
        "END:VTIME\r\n ZONE\r\n"
    )
    content = calendar(EVENT).replace("BEGIN:VEVENT\r\n", zone + "BEGIN:VEVENT\r\n")
    rows, _ = parse(content)
    assert len(rows) == 1 and len(decoded) == 1
    with pytest.raises(TimetableImportError, match="unfolded calendar property"):
        parse(
            calendar(EVENT).replace(
                "SUMMARY:CS 2110 Lecture", "SUMMARY:" + "x" * 12_000 + "\r\n " + "y" * 12_000
            )
        )


@pytest.mark.asyncio
async def test_reupload_changed_same_uid_cannot_duplicate_but_other_source_is_independent(
    student_env,
):
    await register(student_env)

    async def import_file(content):
        preview = (
            await student_env.client.post(
                "/api/v1/student/imports/preview",
                json={
                    "content": content,
                    "term_start": COURSE["term_start"],
                    "term_end": COURSE["term_end"],
                },
            )
        ).json()
        return await student_env.client.post(
            "/api/v1/student/imports/confirm",
            json={
                "preview_id": preview["preview_id"],
                "preview_hash": preview["preview_hash"],
                "request_id": str(uuid4()),
                "confirmed": True,
                "candidates": preview["candidates"],
            },
        )

    assert (await import_file(calendar(EVENT))).status_code == 200
    changed = (
        EVENT.replace("100000", "120000")
        .replace("110000", "130000")
        .replace("CS 2110 Lecture", "New lecture title")
        .replace("Science Hall 101", "Moved Hall 210")
    )
    rejected = await import_file(calendar(changed))
    assert rejected.status_code == 409 and "already saved" in rejected.text
    courses = (await student_env.client.get("/api/v1/student/courses")).json()["courses"]
    assert len(courses) == 1 and len(courses[0]["meetings"]) == 1
    assert courses[0]["meetings"][0]["start_time"] == "10:00"
    other_calendar = calendar(changed).replace(
        "PRODID:-//NavoX tests//EN", "PRODID:-//Other school//EN"
    )
    assert (await import_file(other_calendar)).status_code == 200
    await register(student_env, "other-calendar-owner@example.com")
    assert (await import_file(calendar(EVENT))).status_code == 200


@pytest.mark.asyncio
async def test_preview_saves_no_courses_or_raw_file_then_explicit_confirm_is_atomic_idempotent(
    student_env,
):
    await register(student_env)
    content = calendar(
        EVENT + "\nDESCRIPTION:Private source description never retained\nRRULE:FREQ=WEEKLY;COUNT=3"
    )
    response = await student_env.client.post(
        "/api/v1/student/imports/preview",
        json={
            "content": content,
            "term_start": COURSE["term_start"],
            "term_end": COURSE["term_end"],
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert (await student_env.client.get("/api/v1/student/courses")).json()["courses"] == []
    candidate = {
        **preview["candidates"][0],
        "course_code": "CS 2110",
        "course_name": "Programming",
        "kind": "lecture",
    }
    command = {
        "preview_id": preview["preview_id"],
        "preview_hash": preview["preview_hash"],
        "request_id": str(uuid4()),
        "confirmed": True,
        "candidates": [candidate],
    }
    assert (
        await student_env.client.post(
            "/api/v1/student/imports/confirm", json={**command, "confirmed": 1}
        )
    ).status_code == 422
    response = await student_env.client.post("/api/v1/student/imports/confirm", json=command)
    assert response.status_code == 200, response.text
    assert response.json()["reused"] is False
    course = response.json()["courses"][0]
    assert course["code"] == "CS 2110" and len(course["meetings"][0]["dates"]) == 3
    again = await student_env.client.post("/api/v1/student/imports/confirm", json=command)
    assert again.json()["reused"] is True
    async with student_env.factory() as database:
        stored = await database.get(StudentImportPreview, UUID(preview["preview_id"]))
        assert stored.candidates == []
        assert "Private source" not in str(stored.__dict__)
        meeting = await database.scalar(select(StudentClassMeeting))
        assert meeting.import_key == candidate["id"]
    # A second reviewed copy cannot silently create duplicate sections.
    second = (
        await student_env.client.post(
            "/api/v1/student/imports/preview",
            json={
                "content": content,
                "term_start": COURSE["term_start"],
                "term_end": COURSE["term_end"],
            },
        )
    ).json()
    duplicate = {
        **command,
        "preview_id": second["preview_id"],
        "preview_hash": second["preview_hash"],
        "request_id": str(uuid4()),
    }
    assert (
        await student_env.client.post("/api/v1/student/imports/confirm", json=duplicate)
    ).status_code == 409


@pytest.mark.asyncio
async def test_preview_owner_timezone_and_exact_dates_are_fenced(student_env):
    await register(student_env)
    response = await student_env.client.post(
        "/api/v1/student/imports/preview",
        json={
            "content": calendar(EVENT),
            "term_start": COURSE["term_start"],
            "term_end": COURSE["term_end"],
        },
    )
    preview = response.json()
    command = {
        "preview_id": preview["preview_id"],
        "preview_hash": preview["preview_hash"],
        "request_id": str(uuid4()),
        "confirmed": True,
        "candidates": preview["candidates"],
    }
    changed = {**command, "candidates": [{**preview["candidates"][0], "start_time": "09:30"}]}
    assert (
        await student_env.client.post("/api/v1/student/imports/confirm", json=changed)
    ).status_code == 409
    await student_env.client.put("/api/v1/student/profile", json={"revision": 0, "timezone": "UTC"})
    assert (
        await student_env.client.post("/api/v1/student/imports/confirm", json=command)
    ).status_code == 409
    await register(student_env, "import-other@example.com")
    assert (
        await student_env.client.post("/api/v1/student/imports/confirm", json=command)
    ).status_code == 404
