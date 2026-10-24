"""Mock-wire completeness and privacy checks, never live Microsoft qualification."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from navox.providers import microsoft_availability as provider

START = datetime(2026, 10, 22, 12, tzinfo=UTC)
END = START + timedelta(hours=4)
TOKEN = "synthetic-private-access-token"


def graph_time(value):
    return {"dateTime": value.replace(tzinfo=None).isoformat() + ".0000000", "timeZone": "UTC"}


def event(first, last, show="busy", **changes):
    return {
        "subject": "Private title must never enter a snapshot",
        "start": graph_time(START + timedelta(minutes=first)),
        "end": graph_time(START + timedelta(minutes=last)),
        "showAs": show,
        "isCancelled": False,
        **changes,
    }


async def collect(handler, *, check=None):
    async def allowed():
        return None

    return await provider.MicrosoftAvailabilityProvider(
        transport=httpx.MockTransport(handler)
    ).collect(access_token=TOKEN, start_at=START, end_at=END, check=check or allowed)


def intervals(snapshot):
    def minutes(value):
        return int((value - START).total_seconds() // 60)

    return {(minutes(item.start_at), minutes(item.end_at)) for item in snapshot.busy}


def routes(calendars, views):
    seen = []

    async def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert request.headers["prefer"] == 'outlook.timezone="UTC"'
        path = request.url.path
        if path == "/v1.0/me/calendars":
            return httpx.Response(200, json=calendars)
        name = path.split("/")[-2]
        assert path.endswith("/calendarView")
        assert request.url.params["$select"] == "start,end,showAs,isCancelled"
        assert request.url.params["startDateTime"] == "2026-10-22T12:00:00Z"
        result = views[name]
        if isinstance(result, int):
            return httpx.Response(result)
        return httpx.Response(200, json=result)

    return handler, seen


@pytest.mark.asyncio
async def test_reads_every_calendar_and_keeps_only_blocking_instants():
    handler, seen = routes(
        {"value": [{"id": "cal-a"}, {"id": "cal-b"}]},
        {
            "cal-a": {"value": [event(0, 60), event(90, 120, show="free")]},
            "cal-b": {
                "value": [
                    event(150, 180, show="tentative"),
                    event(200, 230, isCancelled=True),
                    event(-30, 15, show="oof"),
                ]
            },
        },
    )
    snapshot = await collect(handler)
    assert snapshot.state == "complete"
    assert snapshot.calendar_count == 2
    # Free and cancelled events never block; edges are clipped to the window.
    assert intervals(snapshot) == {(0, 60), (150, 180), (0, 15)}
    assert "Private title" not in repr(snapshot)
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_event_pages_are_followed_on_graph_only():
    page_two = "https://graph.microsoft.com/v1.0/me/calendars/cal-a/calendarView?$skip=1"

    async def handler(request):
        if request.url.path == "/v1.0/me/calendars":
            return httpx.Response(200, json={"value": [{"id": "cal-a"}]})
        if "$skip" in request.url.params:
            return httpx.Response(200, json={"value": [event(60, 90)]})
        return httpx.Response(200, json={"value": [event(0, 30)], "@odata.nextLink": page_two})

    snapshot = await collect(handler)
    assert snapshot.state == "complete"
    assert intervals(snapshot) == {(0, 30), (60, 90)}


@pytest.mark.asyncio
async def test_foreign_next_link_makes_coverage_partial():
    async def handler(request):
        if request.url.path == "/v1.0/me/calendars":
            return httpx.Response(200, json={"value": [{"id": "cal-a"}]})
        return httpx.Response(
            200,
            json={"value": [event(0, 30)], "@odata.nextLink": "https://evil.example/next"},
        )

    snapshot = await collect(handler)
    assert snapshot.state == "partial"
    assert intervals(snapshot) == {(0, 30)}


@pytest.mark.asyncio
async def test_one_failing_calendar_is_partial_never_free():
    handler, _ = routes(
        {"value": [{"id": "cal-a"}, {"id": "cal-b"}]},
        {"cal-a": {"value": [event(0, 60)]}, "cal-b": 503},
    )
    snapshot = await collect(handler)
    assert snapshot.state == "partial"
    assert intervals(snapshot) == {(0, 60)}


@pytest.mark.asyncio
async def test_failed_inventory_is_unavailable():
    async def handler(request):
        return httpx.Response(401)

    snapshot = await collect(handler)
    assert snapshot.state == "unavailable"
    assert snapshot.busy == ()


@pytest.mark.asyncio
async def test_unknown_time_zone_or_show_state_is_not_trusted():
    handler, _ = routes(
        {"value": [{"id": "cal-a"}]},
        {
            "cal-a": {
                "value": [
                    {
                        **event(0, 30),
                        "start": {
                            "dateTime": "2026-10-22T12:00:00",
                            "timeZone": "Pacific Standard Time",
                        },
                    },
                    event(60, 90, show="somethingNew"),
                ]
            }
        },
    )
    snapshot = await collect(handler)
    assert snapshot.state == "partial"
    # Unknown show states still block, conservatively.
    assert intervals(snapshot) == {(60, 90)}


@pytest.mark.asyncio
async def test_authority_check_runs_around_every_request():
    calls = []

    async def check():
        calls.append("check")

    handler, seen = routes({"value": [{"id": "cal-a"}]}, {"cal-a": {"value": []}})
    await collect(handler, check=check)
    assert len(calls) == 2 * len(seen)


@pytest.mark.asyncio
async def test_revoked_authority_stops_the_read():
    class Revoked(Exception):
        pass

    async def check():
        raise Revoked

    handler, seen = routes({"value": [{"id": "cal-a"}]}, {"cal-a": {"value": []}})
    with pytest.raises(Revoked):
        await collect(handler, check=check)
    assert seen == []
