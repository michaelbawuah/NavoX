"""Mock-wire completeness and privacy checks, never live Google qualification."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from navox.providers import google_availability as provider

START = datetime(2026, 10, 22, 12, tzinfo=UTC)
END = START + timedelta(hours=2)
TOKEN = "synthetic-private-access-token"


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def calendar(identifier, **changes):
    return {
        "id": identifier,
        "accessRole": "reader",
        "summary": "Private calendar title must never enter a snapshot",
        "hidden": True,
        "selected": False,
        "primary": False,
        **changes,
    }


def period(first=10, last=20):
    return {
        "start": stamp(START + timedelta(minutes=first)),
        "end": stamp(START + timedelta(minutes=last)),
    }


def inventory(identifiers):
    return {
        "items": [calendar(identifier) for identifier in identifiers],
        "nextSyncToken": "synthetic-complete-list-marker",
    }


def freebusy(ids, *, busy=None, **changes):
    return {
        "timeMin": stamp(START),
        "timeMax": stamp(END),
        "calendars": {identifier: {"busy": (busy or {}).get(identifier, [])} for identifier in ids},
        **changes,
    }


async def collect(handler, *, check=None):
    async def allowed():
        return None

    return await provider.GoogleAvailabilityProvider(
        transport=httpx.MockTransport(handler)
    ).collect(access_token=TOKEN, start_at=START, end_at=END, check=check or allowed)


def intervals(snapshot):
    return {(item.start_at, item.end_at) for item in snapshot.busy}


@pytest.mark.asyncio
async def test_all_calendar_pages_include_hidden_and_nonprimary_with_bounded_freebusy_chunks():
    identifiers = [f"private-calendar-{index}@example.test" for index in range(61)]
    requested = []
    checkpoints = []

    async def check():
        checkpoints.append(("check", len(requested)))

    async def handler(request):
        assert request.headers["authorization"] == "Bearer " + TOKEN
        checkpoints.append(("request", len(requested)))
        requested.append(request)
        if request.method == "GET":
            assert request.url.path == "/calendar/v3/users/me/calendarList"
            assert request.url.params["showHidden"] == "true"
            assert request.url.params["maxResults"] == "250"
            assert "minAccessRole" not in request.url.params
            assert "syncToken" not in request.url.params
            if "pageToken" not in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "items": [calendar(identifier) for identifier in identifiers[:31]],
                        "nextPageToken": "private-opaque-page-token",
                    },
                )
            assert request.url.params["pageToken"] == "private-opaque-page-token"
            return httpx.Response(
                200,
                json={
                    "items": [calendar(identifier) for identifier in identifiers[31:]],
                    "nextSyncToken": "private-final-sync-token",
                },
            )
        assert request.method == "POST" and request.url.path == "/calendar/v3/freeBusy"
        body = json.loads(request.content)
        batch = [item["id"] for item in body["items"]]
        assert 1 <= len(batch) <= 50
        assert body["calendarExpansionMax"] == 50
        assert body["groupExpansionMax"] == 1
        assert body["timeZone"] == "UTC"
        assert datetime.fromisoformat(body["timeMin"].replace("Z", "+00:00")) == START
        assert datetime.fromisoformat(body["timeMax"].replace("Z", "+00:00")) == END
        busy = {identifiers[0]: [period()], identifiers[-1]: [period(30, 40)]}
        return httpx.Response(200, json=freebusy(batch, busy=busy))

    snapshot = await collect(handler, check=check)
    assert snapshot.state == "complete" and snapshot.calendar_count == len(identifiers)
    batches = [
        json.loads(request.content)["items"] for request in requested if request.method == "POST"
    ]
    assert sorted(len(batch) for batch in batches) == [11, 50]
    assert {item["id"] for batch in batches for item in batch} == set(identifiers)
    assert intervals(snapshot) == {
        (START + timedelta(minutes=10), START + timedelta(minutes=20)),
        (START + timedelta(minutes=30), START + timedelta(minutes=40)),
    }
    for index, entry in enumerate(checkpoints):
        if entry[0] == "request":
            assert checkpoints[index - 1][0] == "check"
            assert checkpoints[index + 1][0] == "check"
    encoded = repr(snapshot)
    for private in (TOKEN, identifiers[0], "Private calendar title", "private-opaque-page-token"):
        assert private not in encoded
    assert snapshot.observed_at.tzinfo is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["missing-calendar", "calendar-errors", "missing-busy", "invalid-calendar"]
)
async def test_missing_or_failed_calendar_never_turns_into_confirmed_free_time(failure):
    good, unknown = "good@example.test", "unknown@example.test"

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=inventory([good, unknown]))
        body = freebusy([good, unknown], busy={good: [period()]})
        if failure == "missing-calendar":
            body["calendars"].pop(unknown)
        elif failure == "calendar-errors":
            body["calendars"][unknown] = {"errors": [{"reason": "notFound"}]}
        elif failure == "missing-busy":
            body["calendars"][unknown] = {}
        else:
            body["calendars"][unknown] = "invalid-provider-value"
        return httpx.Response(200, json=body)

    snapshot = await collect(handler)
    assert snapshot.state == "partial" and snapshot.calendar_count == 2
    assert intervals(snapshot) == {(START + timedelta(minutes=10), START + timedelta(minutes=20))}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    [
        {"start": "not-an-instant", "end": stamp(END)},
        {"start": "2026-10-22T12:00:00", "end": stamp(END)},
        {"start": stamp(END), "end": stamp(START)},
        {"start": stamp(START), "end": stamp(START)},
        {"start": stamp(START)},
    ],
)
async def test_malformed_busy_keeps_known_blockers_but_withholds_complete_coverage(
    malformed,
):
    identifier = "private-malformed-calendar@example.test"

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=inventory([identifier]))
        return httpx.Response(
            200, json=freebusy([identifier], busy={identifier: [period(), malformed]})
        )

    snapshot = await collect(handler)
    assert snapshot.state != "complete"
    assert intervals(snapshot) == {(START + timedelta(minutes=10), START + timedelta(minutes=20))}
    assert identifier not in repr(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"timeMin": stamp(START + timedelta(minutes=1))},
        {"timeMax": stamp(END - timedelta(minutes=1))},
        {"timeMin": "2026-10-22T12:00:00"},
        {"groups": {"private-group@example.test": {"errors": [{"reason": "groupTooBig"}]}}},
    ],
)
async def test_unproven_response_window_or_group_expansion_is_never_complete(change):
    identifier = "private-window-calendar@example.test"

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=inventory([identifier]))
        return httpx.Response(200, json=freebusy([identifier], **change))

    snapshot = await collect(handler)
    assert snapshot.state != "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["repeat-token", "duplicate-id", "invalid-id", "invalid-items"])
async def test_incomplete_or_ambiguous_calendar_inventory_stays_partial(failure):
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "GET":
            if failure == "repeat-token":
                return httpx.Response(
                    200,
                    json={"items": [calendar("one@example.test")], "nextPageToken": "same-token"},
                )
            if failure == "duplicate-id":
                return httpx.Response(
                    200,
                    json=inventory(["one@example.test", "one@example.test"]),
                )
            if failure == "invalid-id":
                return httpx.Response(200, json=inventory([""]))
            return httpx.Response(
                200, json={"items": "not-an-array", "nextSyncToken": "final-marker"}
            )
        ids = [item["id"] for item in json.loads(request.content)["items"]]
        return httpx.Response(200, json=freebusy(ids))

    snapshot = await collect(handler)
    assert snapshot.state != "complete"
    assert len([item for item in requests if item.method == "GET"]) <= provider.MAX_PAGES


@pytest.mark.asyncio
async def test_page_cap_never_truncates_into_complete_inventory():
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "GET":
            index = len([item for item in requests if item.method == "GET"])
            return httpx.Response(
                200,
                json={
                    "items": [calendar(f"calendar-{index}@example.test")],
                    "nextPageToken": f"next-{index}",
                },
            )
        ids = [item["id"] for item in json.loads(request.content)["items"]]
        return httpx.Response(200, json=freebusy(ids))

    snapshot = await collect(handler)
    assert snapshot.state != "complete"
    assert len([item for item in requests if item.method == "GET"]) == provider.MAX_PAGES


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["calendars", "busy"])
async def test_calendar_and_busy_caps_withhold_completeness(monkeypatch, limit):
    if limit == "calendars":
        monkeypatch.setattr(provider, "MAX_CALENDARS", 2)
    else:
        monkeypatch.setattr(provider, "MAX_BUSY_INTERVALS", 2)

    async def handler(request):
        if request.method == "GET":
            count = 3 if limit == "calendars" else 1
            return httpx.Response(200, json=inventory([str(index) for index in range(count)]))
        ids = [item["id"] for item in json.loads(request.content)["items"]]
        return httpx.Response(
            200, json=freebusy(ids, busy={ids[0]: [period(5, 10), period(15, 20), period(25, 30)]})
        )

    snapshot = await collect(handler)
    assert snapshot.state != "complete"
    if limit == "calendars":
        assert snapshot.calendar_count <= 2
    else:
        assert snapshot.calendar_count == 1 and len(snapshot.busy) <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_initial_provider_failure_is_unavailable_without_private_body_or_retry(status):
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(
            status, json={"error": {"message": "private-provider-body-with-token"}}
        )

    snapshot = await collect(handler)
    assert snapshot.state == "unavailable" and snapshot.calendar_count == 0
    assert snapshot.busy == () and len(seen) == 1
    assert "private-provider-body" not in repr(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "redirect", "oversized", "invalid-json"])
async def test_transport_and_unbounded_response_fail_closed_without_followups(failure):
    seen = []

    async def handler(request):
        seen.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private socket details", request=request)
        if failure == "redirect":
            return httpx.Response(
                302, headers={"location": "https://untrusted.example/collect-token"}
            )
        if failure == "oversized":
            return httpx.Response(
                200, content=b'{"private":"' + b"x" * (provider.MAX_RESPONSE_BYTES + 1) + b'"}'
            )
        return httpx.Response(200, content=b"<private>not JSON</private>")

    snapshot = await collect(handler)
    assert snapshot.state != "complete" and snapshot.busy == ()
    assert len(seen) == 1
    assert "private" not in repr(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [PermissionError, TimeoutError])
async def test_scope_revocation_before_read_prevents_any_provider_request(error_type):
    requests = []
    denied = error_type("Synthetic authority revoked")

    async def check():
        raise denied

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=inventory([]))

    with pytest.raises(error_type) as raised:
        await collect(handler, check=check)
    assert raised.value is denied and requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [PermissionError, TimeoutError])
async def test_scope_revocation_during_provider_io_discards_late_response(error_type):
    requests = []
    revoked = False
    denied = error_type("Synthetic authority revoked during I/O")

    async def check():
        if revoked:
            raise denied

    async def handler(request):
        nonlocal revoked
        requests.append(request)
        revoked = True
        return httpx.Response(200, json=inventory(["private-late@example.test"]))

    with pytest.raises(error_type) as raised:
        await collect(handler, check=check)
    assert raised.value is denied and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport", "oversized"])
async def test_revocation_is_rechecked_after_failed_io_before_returning_unknown_coverage(failure):
    revoked = False
    denied = PermissionError("Synthetic authority revoked while provider failed")

    async def check():
        if revoked:
            raise denied

    async def handler(request):
        nonlocal revoked
        revoked = True
        if failure == "transport":
            raise httpx.ReadTimeout("private error", request=request)
        return httpx.Response(200, content=b"x" * (provider.MAX_RESPONSE_BYTES + 1))

    with pytest.raises(PermissionError) as raised:
        await collect(handler, check=check)
    assert raised.value is denied


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_empty_inventory_requires_final_complete_list_marker(complete):
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json=inventory([]) if complete else {"items": []})

    snapshot = await collect(handler)
    assert snapshot.state == ("complete" if complete else "partial")
    assert snapshot.calendar_count == 0 and snapshot.busy == ()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_cancellation_during_google_read_propagates_and_stops_further_requests():
    entered = asyncio.Event()
    requests = []

    async def handler(request):
        requests.append(request)
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancelled provider read resumed")

    task = asyncio.create_task(collect(handler))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 1
