"""Disabled Microsoft write prototype; synthetic wire evidence, never live qualification."""

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from navox.student_calendars import microsoft_provider as ms
from navox.student_calendars.provider import (
    CalendarWriteChanged,
    CalendarWriteRejected,
    CalendarWriteUncertain,
    GoogleCalendarWriteError,
)
from navox.student_calendars.scheduling import _matches

ACTION = "0123456789abcdef0123456789abcdef"
DIGEST = "a" * 64
CALENDAR = "calendar/+opaque="
REMOTE = "event/+opaque="
ETAG = 'W/"exact-version"'
ZONE = "America/New_York"


async def allowed():
    pass


def review(**changes):
    return {
        "id": ACTION,
        "summary": "Study catch-up",
        "location": "Library",
        "start": {"dateTime": "2026-10-23T10:00:00-04:00", "timeZone": ZONE},
        "end": {"dateTime": "2026-10-23T10:30:00-04:00", "timeZone": ZONE},
        "visibility": "private",
        "transparency": "opaque",
        "reminders": {"useDefault": False},
        "extendedProperties": {"private": {"navoxAction": ACTION, "navoxDigest": DIGEST}},
        **changes,
    }


def calendar(identifier=CALENDAR, **changes):
    return {
        "id": identifier,
        "name": "My calendar",
        "canEdit": True,
        "canShare": True,
        "isSharedWithMe": False,
        "owner": {"address": "student@example.test"},
        **changes,
    }


def raw(property_id=ms.ACTION_PROPERTY, **changes):
    metadata = json.dumps({"v": 1, "action": ACTION, "digest": DIGEST, "timezone": ZONE})
    return {
        "id": REMOTE,
        "@odata.etag": ETAG,
        "subject": "Study catch-up",
        "start": {"dateTime": "2026-10-23T14:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-10-23T14:30:00.0000000", "timeZone": "UTC"},
        "originalStartTimeZone": ZONE,
        "originalEndTimeZone": ZONE,
        "location": {"displayName": "Library"},
        "locations": [{"displayName": "Library"}],
        "body": {"contentType": "text", "content": ms.NOTE},
        "showAs": "busy",
        "sensitivity": "private",
        "isReminderOn": False,
        "isAllDay": False,
        "isCancelled": False,
        "isOnlineMeeting": False,
        "onlineMeeting": None,
        "attendees": [],
        "recurrence": None,
        "type": "singleInstance",
        "hasAttachments": False,
        "responseRequested": False,
        "allowNewTimeProposals": False,
        "transactionId": ACTION,
        "singleValueExtendedProperties": [
            {"id": property_id, "value": ACTION if property_id == ms.ACTION_PROPERTY else metadata}
        ],
        **changes,
    }


def client(handler, **changes):
    return ms.MicrosoftCalendarWriteProvider(transport=httpx.MockTransport(handler), **changes)


def wire(calls):
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-token"
        assert 'IdType="ImmutableId"' in request.headers["prefer"]
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"mail": "student@example.test"})
        if request.method == "POST":
            return httpx.Response(201, json={"id": REMOTE, "@odata.etag": ETAG})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"value": [raw()]})
        if "$expand" in request.url.params:
            prop = (
                ms.DATA_PROPERTY
                if ms.DATA_PROPERTY in request.url.params["$expand"]
                else ms.ACTION_PROPERTY
            )
            return httpx.Response(200, json=raw(prop))
        return httpx.Response(200, json=calendar())

    return handler


@pytest.mark.asyncio
async def test_creation_is_disabled_by_default_and_undo_never_sends_delete():
    calls = []
    provider = client(wire(calls))
    with pytest.raises(CalendarWriteRejected, match="not qualified"):
        await provider.insert(
            access_token="synthetic-token", calendar_id=CALENDAR, event=review(), check=allowed
        )
    with pytest.raises(CalendarWriteRejected, match="Undo is not qualified"):
        await provider.delete(
            access_token="synthetic-token",
            calendar_id=CALENDAR,
            event_id=ACTION,
            etag=ETAG,
            check=allowed,
        )
    assert calls == []
    assert provider.conditional_undo_supported is False


@pytest.mark.asyncio
async def test_exact_personal_create_is_one_post_with_marker_and_versioned_readback():
    calls, checkpoints = [], []

    async def check():
        checkpoints.append(len(calls))

    result = await client(wire(calls), enabled=True).insert(
        access_token="synthetic-token", calendar_id=CALENDAR, event=review(), check=check
    )
    posts = [r for r in calls if r.method == "POST"]
    assert len(posts) == 1
    payload = json.loads(posts[0].content)
    assert payload["transactionId"] == ACTION
    assert payload["attendees"] == []
    assert payload["body"]["content"] == "Added by NavoX"
    assert payload["isOnlineMeeting"] is False
    assert payload["singleValueExtendedProperties"][0] == {
        "id": ms.ACTION_PROPERTY,
        "value": ACTION,
    }
    assert payload["start"] == {"dateTime": "2026-10-23T10:00:00", "timeZone": ZONE}
    assert result["id"] == ACTION and result["etag"] == ETAG
    row = SimpleNamespace(id=UUID(ACTION), digest=DIGEST)
    draft = SimpleNamespace(
        title="Study catch-up",
        location="Library",
        timezone=ZONE,
        start_at=datetime.fromisoformat("2026-10-23T10:00:00-04:00"),
        end_at=datetime.fromisoformat("2026-10-23T10:30:00-04:00"),
    )
    assert _matches(row, draft, result)
    assert len(checkpoints) == 2 * len(calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [409, 429, 500])
async def test_ambiguous_post_is_never_retried(status):
    calls = []
    normal = wire(calls)

    def handler(request):
        if request.method == "POST":
            calls.append(request)
            return httpx.Response(status, json={"private": "never echo this"})
        return normal(request)

    with pytest.raises(CalendarWriteUncertain) as error:
        await client(handler, enabled=True).insert(
            access_token="synthetic-token", calendar_id=CALENDAR, event=review(), check=allowed
        )
    assert "never echo" not in str(error.value)
    assert sum(r.method == "POST" for r in calls) == 1


@pytest.mark.asyncio
async def test_lost_post_response_can_be_reconciled_by_read_only_exact_marker():
    calls = []
    normal = wire(calls)

    def handler(request):
        if request.method == "POST":
            calls.append(request)
            raise httpx.ReadTimeout("private response")
        return normal(request)

    provider = client(handler, enabled=True)
    with pytest.raises(CalendarWriteUncertain):
        await provider.insert(
            access_token="synthetic-token", calendar_id=CALENDAR, event=review(), check=allowed
        )
    before = len(calls)
    result = await provider.event(
        access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
    )
    assert result["id"] == ACTION
    assert all(r.method == "GET" for r in calls[before:])
    assert ACTION in calls[before].url.params["$filter"]
    assert ms.ACTION_PROPERTY in calls[before].url.params["$filter"]
    assert sum(r.method == "POST" for r in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"value": []},
        {"value": [raw(), raw()]},
        {"value": [raw()], "@odata.nextLink": "https://graph.microsoft.com/more"},
    ],
)
async def test_marker_lookup_never_turns_absence_or_partial_or_ambiguity_into_deleted(response):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    with pytest.raises(CalendarWriteUncertain):
        await client(handler).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
        )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"transactionId": "another-action"},
        {"singleValueExtendedProperties": [{"id": ms.ACTION_PROPERTY, "value": ACTION.upper()}]},
    ],
)
async def test_marker_collision_cannot_confirm_an_unrelated_event(changes):
    with pytest.raises(CalendarWriteUncertain):
        await client(lambda r: httpx.Response(200, json={"value": [raw(**changes)]})).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"@odata.etag": 'W/"different"'},
        {"attendees": [{"emailAddress": {"address": "other@example.test"}}]},
        {"sensitivity": "normal"},
        {"isOnlineMeeting": True},
        {"body": {"contentType": "text", "content": "changed"}},
        {"originalStartTimeZone": "UTC"},
    ],
)
async def test_changed_event_or_revision_never_becomes_verified_personal_receipt(changes):
    def handler(request):
        body = (
            raw(ms.DATA_PROPERTY, **changes)
            if request.url.path.endswith(REMOTE)
            else {"value": [raw()]}
        )
        return httpx.Response(200, json=body)

    with pytest.raises((CalendarWriteChanged, CalendarWriteUncertain)):
        await client(handler).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"attendees": []},
        {"recurrence": None},
        {"conferenceData": {}},
        {"unapproved": "data"},
        {
            "start": {"dateTime": "2026-11-01T01:30:00-04:00", "timeZone": ZONE},
            "end": {"dateTime": "2026-11-01T01:45:00-04:00", "timeZone": ZONE},
        },
    ],
)
async def test_unapproved_fields_and_ambiguous_wall_clock_reject_before_any_io(changes):
    calls = []
    with pytest.raises(CalendarWriteRejected):
        await client(wire(calls), enabled=True).insert(
            access_token="synthetic-token",
            calendar_id=CALENDAR,
            event=review(**changes),
            check=allowed,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_inventory_skips_shared_and_read_only_and_requires_verified_owner():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"mail": "student@example.test"})
        return httpx.Response(
            200,
            json={
                "value": [
                    calendar(),
                    calendar("shared", canShare=False, isSharedWithMe=True),
                    calendar("readonly", canEdit=False),
                    calendar("other-owner", owner={"address": "other@example.test"}),
                ]
            },
        )

    result = await client(handler, timezone=ZONE).owned_calendars(
        access_token="synthetic-token", check=allowed
    )
    assert [item.id for item in result] == [CALENDAR]
    assert result[0].timezone == ZONE
    assert all(r.method == "GET" for r in calls)


@pytest.mark.asyncio
async def test_foreign_inventory_link_is_not_followed():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"mail": "student@example.test"})
        return httpx.Response(
            200, json={"value": [], "@odata.nextLink": "https://evil.example/steal"}
        )

    with pytest.raises(GoogleCalendarWriteError):
        await client(handler).owned_calendars(access_token="synthetic-token", check=allowed)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_authority_revoked_after_post_prevents_any_late_receipt_publication():
    calls = []

    class Revoked(Exception):
        pass

    async def check():
        if any(r.method == "POST" for r in calls):
            raise Revoked

    with pytest.raises(Revoked):
        await client(wire(calls), enabled=True).insert(
            access_token="synthetic-token", calendar_id=CALENDAR, event=review(), check=check
        )
    assert [r.method for r in calls].count("POST") == 1
    assert calls[-1].method == "POST"


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [{"id": ".", "@odata.etag": ETAG}, {"id": REMOTE}, {}])
async def test_malformed_success_receipt_cannot_mark_a_submitted_post_failed(receipt):
    calls = []
    normal = wire(calls)

    def handler(request):
        if request.method == "POST":
            calls.append(request)
            return httpx.Response(201, json=receipt)
        return normal(request)

    with pytest.raises(CalendarWriteUncertain):
        await client(handler, enabled=True).insert(
            access_token="synthetic-token", calendar_id=CALENDAR, event=review(), check=allowed
        )
    assert sum(r.method == "POST" for r in calls) == 1
