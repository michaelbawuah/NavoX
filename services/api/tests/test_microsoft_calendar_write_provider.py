"""Default-off Microsoft transport; synthetic wire evidence, never live qualification."""

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
        "description": ms.NOTE,
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


def raw(property_id=ms.ACTION_PROPERTY, *, version=2, **changes):
    metadata = {"v": version, "action": ACTION, "digest": DIGEST, "timezone": ZONE}
    if version == 2:
        metadata["wire_timezone"] = "UTC"
    return {
        "id": REMOTE,
        "@odata.etag": ETAG,
        "subject": "Study catch-up",
        "start": {"dateTime": "2026-10-23T14:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-10-23T14:30:00.0000000", "timeZone": "UTC"},
        "originalStartTimeZone": ZONE if version == 1 else "UTC",
        "originalEndTimeZone": ZONE if version == 1 else "UTC",
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
            {
                "id": property_id,
                "value": ACTION if property_id == ms.ACTION_PROPERTY else json.dumps(metadata),
            }
        ],
        **changes,
    }


def client(handler, **changes):
    return ms.MicrosoftCalendarWriteProvider(transport=httpx.MockTransport(handler), **changes)


def wire(calls):
    posted = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-token"
        assert 'IdType="ImmutableId"' in request.headers["prefer"]
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"mail": "student@example.test"})
        if request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(201, json={"id": REMOTE, "@odata.etag": ETAG})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"value": [raw()]})
        if "$expand" in request.url.params:
            prop = (
                ms.DATA_PROPERTY
                if ms.DATA_PROPERTY in request.url.params["$expand"]
                else ms.ACTION_PROPERTY
            )
            result = raw(prop)
            if posted:
                body = posted[-1]
                result["start"], result["end"] = body["start"], body["end"]
                result["originalStartTimeZone"] = body["start"]["timeZone"]
                result["originalEndTimeZone"] = body["end"]["timeZone"]
                if prop == ms.DATA_PROPERTY:
                    result["singleValueExtendedProperties"] = [
                        body["singleValueExtendedProperties"][1]
                    ]
            return httpx.Response(200, json=result)
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
    assert payload["start"] == {"dateTime": "2026-10-23T14:00:00", "timeZone": "UTC"}
    assert json.loads(payload["singleValueExtendedProperties"][1]["value"]) == {
        "v": 2,
        "action": ACTION,
        "digest": DIGEST,
        "timezone": ZONE,
        "wire_timezone": "UTC",
    }
    assert result["id"] == ACTION and result["etag"] == ETAG
    row = SimpleNamespace(id=UUID(ACTION), digest=DIGEST)
    draft = SimpleNamespace(
        title="Study catch-up",
        location="Library",
        timezone=ZONE,
        start_at=datetime.fromisoformat("2026-10-23T10:00:00-04:00"),
        end_at=datetime.fromisoformat("2026-10-23T10:30:00-04:00"),
    )
    assert _matches(row, draft, result, description=ms.NOTE)
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
    lookup = next(request for request in calls[before:] if "$filter" in request.url.params)
    assert ACTION in lookup.url.params["$filter"]
    assert ms.ACTION_PROPERTY in lookup.url.params["$filter"]
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
    normal = wire(calls)

    def handler(request):
        if request.url.path.endswith("/events"):
            calls.append(request)
            return httpx.Response(200, json=response)
        return normal(request)

    with pytest.raises(CalendarWriteUncertain):
        await client(handler).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
        )
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"transactionId": "another-action"},
        {"singleValueExtendedProperties": [{"id": ms.ACTION_PROPERTY, "value": ACTION.upper()}]},
    ],
)
async def test_marker_collision_cannot_confirm_an_unrelated_event(changes):
    normal = wire([])

    def handler(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"value": [raw(**changes)]})
        return normal(request)

    with pytest.raises(CalendarWriteUncertain):
        await client(handler).event(
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
        {"originalStartTimeZone": ZONE},
    ],
)
async def test_changed_event_or_revision_never_becomes_verified_personal_receipt(changes):
    normal = wire([])

    def handler(request):
        if request.url.path.endswith(REMOTE):
            return httpx.Response(200, json=raw(ms.DATA_PROPERTY, **changes))
        return normal(request)

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
        {"description": "Other provenance"},
    ],
)
async def test_unapproved_fields_reject_before_any_io(changes):
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
@pytest.mark.parametrize(
    "following",
    [
        "https://evil.example/steal",
        "https://[invalid-ipv6/v1.0/me/calendars",
        ms.CALENDARS + "?$skiptoken=next#unapproved-fragment",
    ],
)
async def test_foreign_or_malformed_inventory_link_is_not_followed(following):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"mail": "student@example.test"})
        return httpx.Response(200, json={"value": [], "@odata.nextLink": following})

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


@pytest.mark.asyncio
async def test_legacy_v1_marker_retains_positive_only_recovery_without_new_provenance_claim():
    calls = []
    normal = wire(calls)

    def handler(request):
        if request.url.path.endswith("/events"):
            calls.append(request)
            return httpx.Response(200, json={"value": [raw(version=1)]})
        if request.url.path.endswith(REMOTE):
            calls.append(request)
            return httpx.Response(200, json=raw(ms.DATA_PROPERTY, version=1))
        return normal(request)

    result = await client(handler).event(
        access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
    )
    assert result["description"] == "" and result["id"] == ACTION
    assert result["start"]["timeZone"] == ZONE
    assert len(calls) == 4 and all(request.method == "GET" for request in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start,end,utc_start,utc_end",
    [
        (
            "2026-11-01T01:30:00-04:00",
            "2026-11-01T01:45:00-04:00",
            "2026-11-01T05:30:00",
            "2026-11-01T05:45:00",
        ),
        (
            "2026-11-01T01:30:00-04:00",
            "2026-11-01T01:15:00-05:00",
            "2026-11-01T05:30:00",
            "2026-11-01T06:15:00",
        ),
    ],
)
async def test_dst_fold_is_one_exact_utc_write_with_the_reviewed_display_zone(
    start, end, utc_start, utc_end
):
    calls = []
    result = await client(wire(calls), enabled=True).insert(
        access_token="synthetic-token",
        calendar_id=CALENDAR,
        event=review(
            start={"dateTime": start, "timeZone": ZONE},
            end={"dateTime": end, "timeZone": ZONE},
        ),
        check=allowed,
    )
    posts = [request for request in calls if request.method == "POST"]
    assert len(posts) == 1
    payload = json.loads(posts[0].content)
    assert payload["start"] == {"dateTime": utc_start, "timeZone": "UTC"}
    assert payload["end"] == {"dateTime": utc_end, "timeZone": "UTC"}
    assert result["start"]["timeZone"] == result["end"]["timeZone"] == ZONE
    assert datetime.fromisoformat(result["start"]["dateTime"]) == datetime.fromisoformat(start)
    assert datetime.fromisoformat(result["end"]["dateTime"]) == datetime.fromisoformat(end)


@pytest.mark.asyncio
async def test_recovery_rechecks_exact_target_ownership_before_loading_marked_events():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"mail": "student@example.test"})
        return httpx.Response(200, json=calendar(owner={"address": "other@example.test"}))

    with pytest.raises(CalendarWriteRejected):
        await client(handler).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
        )
    assert len(calls) == 2 and not any("/events" in request.url.path for request in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_recovery_http_missing_is_unknown_and_never_absence(status):
    calls = []
    normal = wire(calls)

    def handler(request):
        if request.url.path.endswith("/events"):
            calls.append(request)
            return httpx.Response(status, json={"error": {"message": "private provider text"}})
        return normal(request)

    with pytest.raises(GoogleCalendarWriteError) as error:
        await client(handler).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
        )
    assert "private provider" not in str(error.value)
    assert all(request.method == "GET" for request in calls)


@pytest.mark.asyncio
async def test_provider_submicrosecond_time_is_not_silently_truncated_into_an_exact_review():
    normal = wire([])

    def handler(request):
        if request.url.path.endswith(REMOTE):
            return httpx.Response(
                200,
                json=raw(
                    ms.DATA_PROPERTY,
                    start={"dateTime": "2026-10-23T14:00:00.0000001", "timeZone": "UTC"},
                ),
            )
        return normal(request)

    with pytest.raises(CalendarWriteUncertain):
        await client(handler).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"v": True, "action": ACTION, "digest": DIGEST, "timezone": ZONE},
        {"v": 2, "action": ACTION, "digest": DIGEST, "timezone": ZONE},
        {
            "v": 2,
            "action": ACTION,
            "digest": DIGEST,
            "timezone": ZONE,
            "wire_timezone": ZONE,
        },
        {
            "v": 2,
            "action": ACTION,
            "digest": DIGEST,
            "timezone": ZONE,
            "wire_timezone": "UTC",
            "unreviewed": "data",
        },
    ],
)
async def test_incomplete_or_unknown_review_marker_cannot_establish_a_positive_receipt(metadata):
    normal = wire([])

    def handler(request):
        if request.url.path.endswith(REMOTE):
            return httpx.Response(
                200,
                json=raw(
                    ms.DATA_PROPERTY,
                    singleValueExtendedProperties=[
                        {"id": ms.DATA_PROPERTY, "value": json.dumps(metadata)}
                    ],
                ),
            )
        return normal(request)

    with pytest.raises(CalendarWriteUncertain):
        await client(handler).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=allowed
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,url",
    [
        ("GET", "https://other.example/v1.0/me/calendars"),
        ("GET", "https://graph.microsoft.com/v1.0/users/other/events"),
        ("DELETE", ms.CALENDARS + "/calendar/events/event"),
    ],
)
async def test_transport_cannot_publish_credentials_or_deletes_outside_owned_read_create_routes(
    method, url
):
    calls = []
    with pytest.raises(CalendarWriteRejected):
        await client(wire(calls), enabled=True)._request(
            access_token="synthetic-token", method=method, url=url, check=allowed
        )
    assert calls == []


@pytest.mark.asyncio
async def test_new_create_requires_exact_reviewed_provenance_before_any_provider_io():
    calls = []
    event = review()
    del event["description"]
    with pytest.raises(CalendarWriteRejected):
        await client(wire(calls), enabled=True).insert(
            access_token="synthetic-token", calendar_id=CALENDAR, event=event, check=allowed
        )
    assert calls == []


@pytest.mark.asyncio
async def test_oversized_success_after_post_stays_uncertain_without_another_post(monkeypatch):
    calls = []
    normal = wire(calls)
    monkeypatch.setattr(ms, "MAX_RESPONSE_BYTES", 1024)

    def handler(request):
        if request.method == "POST":
            calls.append(request)
            return httpx.Response(201, content=b"x" * 1025)
        return normal(request)

    with pytest.raises(CalendarWriteUncertain):
        await client(handler, enabled=True).insert(
            access_token="synthetic-token", calendar_id=CALENDAR, event=review(), check=allowed
        )
    assert sum(request.method == "POST" for request in calls) == 1
    assert calls[-1].method == "POST"


@pytest.mark.asyncio
async def test_inventory_page_bound_stops_before_unbounded_calendar_discovery():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"mail": "student@example.test"})
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.nextLink": ms.CALENDARS + "?$skiptoken=" + str(len(calls)),
            },
        )

    with pytest.raises(GoogleCalendarWriteError, match="incomplete"):
        await client(handler).owned_calendars(access_token="synthetic-token", check=allowed)
    assert len(calls) == 5 and all(request.method == "GET" for request in calls)


@pytest.mark.asyncio
async def test_oversized_inventory_cannot_be_treated_as_complete_owned_targets():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"mail": "student@example.test"})
        return httpx.Response(200, json={"value": [calendar(str(i)) for i in range(101)]})

    with pytest.raises(GoogleCalendarWriteError):
        await client(handler).owned_calendars(access_token="synthetic-token", check=allowed)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_revoked_native_authority_prevents_any_read_or_create_request():
    calls = []

    class Revoked(Exception):
        pass

    async def check():
        raise Revoked

    with pytest.raises(Revoked):
        await client(wire(calls)).event(
            access_token="synthetic-token", calendar_id=CALENDAR, event_id=ACTION, check=check
        )
    assert calls == []
