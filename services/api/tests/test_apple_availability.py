"""Synthetic CalDAV protocol/safety evidence, never live iCloud qualification."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from navox.providers import apple_availability as provider

START = datetime(2026, 10, 26, 12, tzinfo=UTC)
END = START + timedelta(hours=2)
ACCOUNT = "synthetic-private-account@example.test"
PASSWORD = "aaaa-bbbb-cccc-dddd"
HOST = "https://p01-caldav.icloud.com"
HOME = HOST + "/private-owner/calendars/"
CALENDAR = HOME + "private-calendar/"
SECOND = HOME + "private-second/"

NS = 'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"'


def multi(*responses):
    return f"<d:multistatus {NS}>" + "".join(responses) + "</d:multistatus>"


def response(href, properties, *, status="HTTP/1.1 200 OK"):
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{properties}</d:prop>"
        f"<d:status>{status}</d:status></d:propstat></d:response>"
    )


def discovery():
    return multi(
        response(
            "/",
            "<d:current-user-principal><d:href>"
            + HOST
            + "/private-owner/principal/</d:href></d:current-user-principal>",
        )
    )


def home():
    return multi(
        response(
            "/private-owner/principal/",
            f"<c:calendar-home-set><d:href>{HOME}</d:href></c:calendar-home-set>",
        )
    )


def inventory(*identifiers):
    return multi(
        response(HOME, "<d:resourcetype><d:collection/></d:resourcetype>"),
        *(
            response(
                identifier,
                "<d:resourcetype><d:collection/><c:calendar/>"
                "</d:resourcetype><c:supported-calendar-component-set>"
                '<c:comp name="VEVENT"/></c:supported-calendar-component-set>',
            )
            for identifier in identifiers
        ),
    )


def freebusy(*lines, first="20261026T120000Z", last="20261026T140000Z"):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Synthetic//EN\r\n"
        f"BEGIN:VFREEBUSY\r\nDTSTART:{first}\r\nDTEND:{last}\r\n"
        + "\r\n".join(lines)
        + "\r\nEND:VFREEBUSY\r\nEND:VCALENDAR\r\n"
    )


def handler_for(*, calendars=(CALENDAR,), report=None, change=None):
    async def handler(request):
        if change is not None:
            changed = change(request)
            if changed is not None:
                return changed
        if request.method == "REPORT":
            return httpx.Response(
                200,
                text=report
                if report is not None
                else freebusy("FREEBUSY:20261026T121000Z/20261026T122000Z"),
            )
        if request.url.host == "caldav.icloud.com":
            return httpx.Response(207, text=discovery())
        if request.url.path.endswith("/principal/"):
            return httpx.Response(207, text=home())
        assert str(request.url) == HOME
        return httpx.Response(207, text=inventory(*calendars))

    return handler


async def collect(handler=None, *, check=None, **changes):
    async def allowed():
        pass

    return await provider.AppleAvailabilityProvider(
        transport=httpx.MockTransport(handler or handler_for())
    ).collect(
        apple_id=ACCOUNT,
        app_password=PASSWORD,
        start_at=START,
        end_at=END,
        check=check or allowed,
        **changes,
    )


@pytest.mark.asyncio
async def test_discovers_all_calendars_and_uses_only_server_expanded_busy_reports():
    requests = []
    checkpoints = []
    inner = handler_for(calendars=(CALENDAR, SECOND))

    async def check():
        checkpoints.append("check")

    async def handler(request):
        checkpoints.append("request")
        requests.append(request)
        assert request.headers["authorization"] == (
            "Basic " + base64.b64encode(f"{ACCOUNT}:{PASSWORD}".encode()).decode()
        )
        if request.method == "REPORT":
            assert request.headers["depth"] == "1"
            assert b"free-busy-query" in request.content
            assert b"20261026T120000Z" in request.content
            assert b"20261026T140000Z" in request.content
            assert b"calendar-data" not in request.content
        return await inner(request)

    snapshot = await collect(handler, check=check)
    assert snapshot.state == "complete" and snapshot.calendar_count == 2
    assert len(snapshot.busy) == 2
    assert snapshot.busy[0].start_at == START + timedelta(minutes=10)
    assert snapshot.busy[0].end_at == START + timedelta(minutes=20)
    assert {str(r.url) for r in requests if r.method == "REPORT"} == {CALENDAR, SECOND}
    assert checkpoints == ["check", "request", "check"] * 5
    for private in (ACCOUNT, PASSWORD, "private-owner", "private-calendar", HOST):
        assert private not in repr(snapshot)


@pytest.mark.asyncio
async def test_multiple_periods_durations_free_flags_and_window_clipping():
    snapshot = await collect(
        handler_for(
            report=freebusy(
                "FREEBUSY:20261026T115000Z/20261026T121000Z,20261026T123000Z/PT30M",
                "FREEBUSY;FBTYPE=FREE:20261026T130000Z/PT10M",
                "FREEBUSY;FBTYPE=BUSY-TENTATIVE:20261026T135000Z/20261026T141000Z",
            )
        )
    )
    assert snapshot.state == "complete"
    assert [(x.start_at, x.end_at) for x in snapshot.busy] == [
        (START, START + timedelta(minutes=10)),
        (START + timedelta(minutes=30), START + timedelta(minutes=60)),
        (END - timedelta(minutes=10), END),
    ]


@pytest.mark.asyncio
async def test_verified_empty_calendar_home_and_empty_busy_report_are_complete():
    empty_home = await collect(handler_for(calendars=()))
    empty_busy = await collect(handler_for(report=freebusy()))
    assert empty_home.state == "complete" and empty_home.calendar_count == 0
    assert empty_busy.state == "complete" and empty_busy.calendar_count == 1
    assert empty_home.busy == empty_busy.busy == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 507])
async def test_unsupported_or_failed_report_is_partial_never_free(status):
    def change(request):
        if request.method == "REPORT":
            return httpx.Response(status, text="private provider error")

    snapshot = await collect(handler_for(change=change))
    assert snapshot.state == "partial" and snapshot.busy == ()
    assert snapshot.calendar_count == 1 and "private" not in repr(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/collect",
        "https://p01-caldav.icloud.com.evil.example/collect",
        "https://caldav.icloud.com@evil.example/collect",
        "https://user:password@caldav.icloud.com/",
        "http://caldav.icloud.com/",
        "https://caldav.icloud.com:8443/",
        "//127.0.0.1/private",
        "https://caldav.icloud.com/?private",
        "https://caldav.icloud.com/#private",
        r"https://caldav.icloud.com\@evil.example/",
    ],
)
async def test_untrusted_discovery_links_never_receive_credentials(url):
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(
            207,
            text=multi(
                response(
                    "/",
                    f"<d:current-user-principal><d:href>{url}</d:href></d:current-user-principal>",
                )
            ),
        )

    snapshot = await collect(handler)
    assert snapshot.state == "unavailable" and len(seen) == 1


@pytest.mark.asyncio
async def test_allowlisted_partition_redirect_is_bounded_and_rechecked():
    requests = []
    inner = handler_for()

    async def handler(request):
        requests.append(request)
        if request.url.host == "caldav.icloud.com":
            return httpx.Response(301, headers={"location": HOST + "/"})
        if request.url.path == "/":
            return httpx.Response(207, text=discovery())
        return await inner(request)

    assert (await collect(handler)).state == "complete"
    assert len(requests) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["https://evil.example/", provider.DISCOVERY_URL])
async def test_redirects_cannot_leak_credentials_or_loop_without_bound(destination):
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": destination})

    assert (await collect(handler)).state == "unavailable"
    assert len(seen) <= provider.MAX_REDIRECTS + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        '<!DOCTYPE d [<!ENTITY private "leak">]><d:multistatus xmlns:d="DAV:"/>',
        "<not-xml",
        "<private/>",
        '<?xml version="1.0" encoding="utf-16"?><private/>',
    ],
)
async def test_untrusted_xml_never_becomes_empty_complete_coverage(body):
    async def handler(request):
        return httpx.Response(207, content=body.encode())

    assert (await collect(handler)).state == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken",
    [
        inventory(CALENDAR, CALENDAR),
        inventory(CALENDAR).replace("HTTP/1.1 200 OK", "HTTP/1.1 403 Forbidden"),
        inventory(CALENDAR).replace("<c:calendar/>", ""),
        inventory(CALENDAR).replace('name="VEVENT"', 'name=""'),
    ],
)
async def test_ambiguous_or_failed_calendar_inventory_is_not_complete(broken):
    def change(request):
        if str(request.url) == HOME:
            return httpx.Response(207, text=broken)

    assert (await collect(handler_for(change=change))).state != "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        freebusy(first="20261026T120001Z"),
        freebusy(last="20261026T135959Z"),
        freebusy("FREEBUSY:20261026T122000Z/20261026T121000Z"),
        freebusy("FREEBUSY:20261026T121000/20261026T122000"),
        freebusy("FREEBUSY:invalid/invalid"),
        freebusy().replace("VFREEBUSY", "VEVENT"),
        freebusy().replace("END:VCALENDAR", "BEGIN:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR"),
        "private invalid iCalendar",
    ],
)
async def test_malformed_or_unproven_freebusy_response_stays_partial(body):
    snapshot = await collect(handler_for(report=body))
    assert snapshot.state == "partial" and snapshot.busy == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before", "during", "failed"])
async def test_authority_revocation_propagates_and_disposes_late_provider_results(stage):
    seen = []
    revoked = stage == "before"
    denied = PermissionError("synthetic authority revoked")

    async def check():
        if revoked:
            raise denied

    async def handler(request):
        nonlocal revoked
        seen.append(request)
        revoked = True
        if stage == "failed":
            raise httpx.ReadTimeout("private transport body", request=request)
        return httpx.Response(207, text=discovery())

    with pytest.raises(PermissionError) as raised:
        await collect(handler, check=check)
    assert raised.value is denied and len(seen) == (0 if stage == "before" else 1)


@pytest.mark.asyncio
async def test_authority_timeout_is_not_treated_as_provider_unknown():
    async def check():
        raise TimeoutError("synthetic owner check timed out")

    with pytest.raises(TimeoutError):
        await collect(check=check)


@pytest.mark.asyncio
async def test_cancellation_stops_discovery_without_returning_partial_success():
    entered = asyncio.Event()
    requests = []

    async def handler(request):
        requests.append(request)
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(collect(handler))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_response_and_inventory_limits_withhold_complete_coverage(monkeypatch):
    async def oversized(request):
        return httpx.Response(207, content=b"x" * (provider.MAX_RESPONSE_BYTES + 1))

    assert (await collect(oversized)).state == "unavailable"
    monkeypatch.setattr(provider, "MAX_CALENDARS", 1)
    assert (await collect(handler_for(calendars=(CALENDAR, SECOND)))).state != "complete"
    monkeypatch.setattr(provider, "MAX_BUSY_INTERVALS", 1)
    assert (
        await collect(
            handler_for(
                report=freebusy("FREEBUSY:20261026T121000Z/20261026T122000Z,20261026T123000Z/PT30M")
            )
        )
    ).state == "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"start_at": START.replace(tzinfo=None)},
        {"end_at": START},
        {"end_at": START + timedelta(days=32)},
        {"start_at": START.replace(microsecond=1)},
        {"app_password": "primary-password"},
        {"apple_id": "private:username"},
    ],
)
async def test_invalid_credentials_or_windows_fail_before_network(change):
    requests = []

    async def handler(request):
        requests.append(request)
        raise AssertionError("Invalid input reached provider")

    values = dict(apple_id=ACCOUNT, app_password=PASSWORD, start_at=START, end_at=END)
    values.update(change)

    async def check():
        pass

    with pytest.raises(ValueError):
        await provider.AppleAvailabilityProvider(transport=httpx.MockTransport(handler)).collect(
            **values, check=check
        )
    assert requests == []


@pytest.mark.asyncio
async def test_late_calendar_failure_keeps_known_blockers_but_never_claims_complete():
    def change(request):
        if str(request.url) == SECOND:
            return httpx.Response(403, text="private denied body")

    snapshot = await collect(handler_for(calendars=(CALENDAR, SECOND), change=change))
    assert snapshot.state == "partial" and snapshot.calendar_count == 2
    assert len(snapshot.busy) == 1


@pytest.mark.asyncio
async def test_explicit_non_event_calendar_does_not_create_a_fake_busy_source():
    def change(request):
        if str(request.url) == HOME:
            return httpx.Response(
                207, text=inventory(CALENDAR).replace('name="VEVENT"', 'name="VTODO"')
            )

    snapshot = await collect(handler_for(change=change))
    assert snapshot.state == "complete" and snapshot.calendar_count == 0


@pytest.mark.asyncio
async def test_caller_timeout_after_io_is_never_masked_as_provider_failure():
    checks = 0

    async def check():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise TimeoutError("synthetic authority timeout")

    with pytest.raises(TimeoutError):
        await collect(check=check)


@pytest.mark.asyncio
async def test_total_provider_deadline_returns_unknown_and_cancels_provider_io(monkeypatch):
    monkeypatch.setattr(provider, "TOTAL_SECONDS", 0.01)

    async def handler(request):
        await asyncio.Event().wait()

    snapshot = await collect(handler)
    assert snapshot.state == "unavailable" and snapshot.busy == ()
