"""Mock-wire calendar write checks; these do not qualify a live Google account."""

import json

import httpx
import pytest

from navox.student_calendars import provider

TOKEN = "synthetic-calendar-access-token"
CALENDAR = "private/calendar+name@example.test"
EVENT_ID = "0123456789abcdef0123456789abcdef"


async def allowed():
    return None


def target(identifier=CALENDAR, **changes):
    return {
        "id": identifier,
        "summary": "Private calendar name",
        "timeZone": "America/New_York",
        "accessRole": "owner",
        **changes,
    }


def event(**changes):
    return {
        "id": EVENT_ID,
        "etag": '"exact-event-version"',
        "summary": "Reviewed personal meeting",
        "start": {"dateTime": "2026-10-23T10:00:00-04:00"},
        "end": {"dateTime": "2026-10-23T10:30:00-04:00"},
        "status": "confirmed",
        **changes,
    }


def client(handler):
    return provider.GoogleCalendarWriteProvider(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_owned_inventory_reads_every_bounded_page_with_owner_filter_and_checkpoints():
    calls, checkpoints = [], []

    async def check():
        checkpoints.append(len(calls))

    def handler(request):
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert request.url.params["minAccessRole"] == "owner"
        assert request.url.params["showHidden"] == "true"
        assert request.url.params["showDeleted"] == "false"
        assert request.url.params["maxResults"] == "100"
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"items": [target()], "nextPageToken": "page-two"})
        assert request.url.params["pageToken"] == "page-two"
        return httpx.Response(200, json={"items": [target("secondary@example.test")]})

    calendars = await client(handler).owned_calendars(access_token=TOKEN, check=check)
    assert tuple(item.id for item in calendars) == (CALENDAR, "secondary@example.test")
    assert calendars[0].label == "Private calendar name"
    assert calendars[0].timezone == "America/New_York"
    assert checkpoints == [0, 1, 1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": {}},
        {"items": [None]},
        {"items": [target(accessRole="writer")]},
        {"items": [target(deleted=True)]},
        {"items": [target(timeZone="Not/AZone")]},
        {"items": [target(), target()]},
        {"items": [target(summary="\nprivate")]},
        {"items": [target("many" + str(index)) for index in range(101)]},
    ],
)
async def test_inventory_never_returns_partial_or_malformed_inventory(body):
    with pytest.raises(provider.GoogleCalendarWriteError):
        await client(lambda _: httpx.Response(200, json=body)).owned_calendars(
            access_token=TOKEN, check=allowed
        )


@pytest.mark.asyncio
async def test_inventory_repeated_token_is_rejected_without_unbounded_paging():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [], "nextPageToken": "repeated"})

    with pytest.raises(provider.GoogleCalendarWriteError):
        await client(handler).owned_calendars(access_token=TOKEN, check=allowed)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_inventory_four_page_limit_is_enforced():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [], "nextPageToken": f"page-{len(calls)}"})

    with pytest.raises(provider.GoogleCalendarWriteError):
        await client(handler).owned_calendars(access_token=TOKEN, check=allowed)
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_target_revalidates_exact_calendar_owner_role_and_encodes_path():
    def handler(request):
        assert request.url.raw_path.split(b"?")[0].endswith(
            b"/calendarList/private%2Fcalendar%2Bname%40example.test"
        )
        return httpx.Response(200, json=target())

    assert (
        await client(handler).target(access_token=TOKEN, calendar_id=CALENDAR, check=allowed)
    ).id == CALENDAR
    with pytest.raises(provider.CalendarWriteRejected):
        await client(lambda _: httpx.Response(200, json=target("different"))).target(
            access_token=TOKEN, calendar_id=CALENDAR, check=allowed
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_event_absence_is_exact_get_only(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"private-error": "not exposed"})

    assert (
        await client(handler).event(
            access_token=TOKEN, calendar_id=CALENDAR, event_id=EVENT_ID, check=allowed
        )
        is None
    )
    assert len(calls) == 1 and calls[0].method == "GET"


@pytest.mark.asyncio
async def test_cancelled_event_returns_none_but_wrong_identifier_and_missing_etag_are_rejected():
    assert (
        await client(
            lambda _: httpx.Response(200, json={"id": EVENT_ID, "status": "cancelled"})
        ).event(access_token=TOKEN, calendar_id=CALENDAR, event_id=EVENT_ID, check=allowed)
        is None
    )
    for body in (event(id="different"), event(etag="")):
        with pytest.raises(provider.GoogleCalendarWriteError):
            await client(lambda _, body=body: httpx.Response(200, json=body)).event(
                access_token=TOKEN, calendar_id=CALENDAR, event_id=EVENT_ID, check=allowed
            )


@pytest.mark.asyncio
async def test_insert_is_single_post_with_exact_body_and_explicit_no_guest_updates():
    calls = []
    body = event()
    body.pop("etag")
    body.pop("status")

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.params["sendUpdates"] == "none"
        assert request.url.raw_path.split(b"?")[0].endswith(
            b"/calendars/private%2Fcalendar%2Bname%40example.test/events"
        )
        assert json.loads(request.content) == body
        return httpx.Response(200, json=event())

    inserted = await client(handler).insert(
        access_token=TOKEN, calendar_id=CALENDAR, event=body, check=allowed
    )
    assert inserted == event() and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["attendees", "recurrence", "conferenceData", "attachments"])
async def test_insert_rejects_unreviewed_invitation_and_series_features_without_io(field):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=event())

    with pytest.raises(provider.CalendarWriteRejected):
        await client(handler).insert(
            access_token=TOKEN, calendar_id=CALENDAR, event=event(**{field: []}), check=allowed
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 503])
async def test_ambiguous_insert_response_never_retries_and_is_not_success(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private provider response must not escape")

    with pytest.raises(provider.CalendarWriteUncertain) as error:
        await client(handler).insert(
            access_token=TOKEN, calendar_id=CALENDAR, event=event(), check=allowed
        )
    assert len(calls) == 1
    assert error.value.status_code == status
    assert "private" not in str(error.value) and TOKEN not in str(error.value)


@pytest.mark.asyncio
async def test_transport_failure_rechecks_native_authority_and_never_retries():
    calls, checks = [], []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private URL and token must not escape")

    async def check():
        checks.append(len(calls))

    with pytest.raises(provider.CalendarWriteUncertain):
        await client(handler).insert(
            access_token=TOKEN, calendar_id=CALENDAR, event=event(), check=check
        )
    assert checks == [0, 1] and len(calls) == 1


@pytest.mark.asyncio
async def test_native_authority_loss_after_response_wins_over_provider_error():
    calls = []

    async def check():
        if calls:
            raise PermissionError("native authority withdrawn")

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(PermissionError, match="native authority withdrawn"):
        await client(handler).insert(
            access_token=TOKEN, calendar_id=CALENDAR, event=event(), check=check
        )


@pytest.mark.asyncio
async def test_delete_uses_exact_etag_and_does_not_replay_after_precondition_failure():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "DELETE"
        assert request.headers["If-Match"] == '"exact-event-version"'
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert request.url.params["sendUpdates"] == "none"
        return httpx.Response(412, text="changed private event")

    with pytest.raises(provider.CalendarWriteChanged):
        await client(handler).delete(
            access_token=TOKEN,
            calendar_id=CALENDAR,
            event_id=EVENT_ID,
            etag='"exact-event-version"',
            check=allowed,
        )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [204, 404, 410])
async def test_delete_single_attempt_accepts_empty_acknowledgement_or_exact_absence(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    await client(handler).delete(
        access_token=TOKEN,
        calendar_id=CALENDAR,
        event_id=EVENT_ID,
        etag='"exact-event-version"',
        check=allowed,
    )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b'{"id":"one","id":"two"}', b"[]", b"{", b"x" * (provider.MAX_RESPONSE_BYTES + 1)]
)
async def test_unverifiable_write_json_and_oversize_response_are_uncertain(body):
    with pytest.raises(provider.CalendarWriteUncertain):
        await client(lambda _: httpx.Response(200, content=body)).insert(
            access_token=TOKEN, calendar_id=CALENDAR, event=event(), check=allowed
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 410, 413, 422])
async def test_definite_rejections_do_not_leak_provider_bodies_or_follow_redirect(
    status,
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://attacker.example"}, text=TOKEN)

    with pytest.raises(provider.CalendarWriteRejected) as error:
        await client(handler).insert(
            access_token=TOKEN, calendar_id=CALENDAR, event=event(), check=allowed
        )
    assert len(calls) == 1 and TOKEN not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize("status", [201, 202, 206, 300, 301, 302, 303, 307, 308, 408, 409, 425])
async def test_unexpected_write_responses_remain_uncertain_without_following_or_replaying(
    method, status
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://attacker.example/must-not-follow"},
            json=event(),
        )

    instance = client(handler)
    with pytest.raises(provider.CalendarWriteUncertain) as error:
        if method == "POST":
            await instance.insert(
                access_token=TOKEN, calendar_id=CALENDAR, event=event(), check=allowed
            )
        else:
            await instance.delete(
                access_token=TOKEN,
                calendar_id=CALENDAR,
                event_id=EVENT_ID,
                etag='"exact-event-version"',
                check=allowed,
            )
    assert len(calls) == 1 and calls[0].method == method
    assert error.value.status_code == status and TOKEN not in str(error.value)


@pytest.mark.asyncio
async def test_delete_success_shaped_200_response_is_uncertain_instead_of_acknowledged():
    with pytest.raises(provider.CalendarWriteUncertain):
        await client(lambda _: httpx.Response(200, json=event())).delete(
            access_token=TOKEN,
            calendar_id=CALENDAR,
            event_id=EVENT_ID,
            etag='"exact-event-version"',
            check=allowed,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "etag", ["*", "", "unquoted", 'W/"weak-version"', '"bad\r\nheader"', '"private-💥"']
)
async def test_delete_refuses_wildcard_weak_and_non_header_safe_versions_before_io(etag):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(204)

    with pytest.raises(provider.CalendarWriteRejected):
        await client(handler).delete(
            access_token=TOKEN, calendar_id=CALENDAR, event_id=EVENT_ID, etag=etag, check=allowed
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("calendar_id", [".", ".."])
async def test_calendar_dot_segments_cannot_change_the_request_path(calendar_id):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=target(calendar_id))

    with pytest.raises(provider.CalendarWriteRejected):
        await client(handler).target(access_token=TOKEN, calendar_id=calendar_id, check=allowed)
    assert calls == []
