"""CalDAV wire fixtures; no real Apple credentials or provider traffic."""

import base64
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from icalendar import Calendar

from navox.providers.apple_availability import DAV, _properties, _Unavailable, _xml
from navox.student.service import StudentError
from navox.student_calendars import apple_provider as apple
from navox.student_calendars.provider import (
    CalendarCreateReceipt,
    CalendarWriteChanged,
    CalendarWriteRejected,
    CalendarWriteUncertain,
    GoogleCalendarWriteError,
)

ACCOUNT = "student@example.test"
PASSWORD = "aaaa-bbbb-cccc-dddd"
PRINCIPAL = "https://p42-caldav.icloud.com/123/principal/"
HOME = "https://p42-caldav.icloud.com/123/calendars/"
TARGET = HOME + "personal/"
ACTION = UUID("11111111-2222-4333-8444-555555555555").hex
DIGEST = "a" * 64
RESOURCE = TARGET + ACTION + ".ics"
AUTH = "Basic " + base64.b64encode(f"{ACCOUNT}:{PASSWORD}".encode()).decode()


def xml_response(href, properties):
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{properties}</d:prop>"
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


def multi(*rows):
    return (
        '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        + "".join(rows)
        + "</d:multistatus>"
    ).encode()


def collection(*, owner=PRINCIPAL, privileges=("read", "bind", "unbind", "write-content")):
    return xml_response(
        TARGET,
        "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
        "<d:displayname>Personal study calendar</d:displayname>"
        f"<d:owner><d:href>{owner}</d:href></d:owner>"
        "<d:current-user-privilege-set>"
        + "".join(f"<d:privilege><d:{value}/></d:privilege>" for value in privileges)
        + "</d:current-user-privilege-set>"
        '<c:supported-calendar-component-set><c:comp name="VEVENT"/>'
        "</c:supported-calendar-component-set>",
    )


def reviewed(**changes):
    start = datetime(2030, 11, 3, 5, 10, tzinfo=UTC)
    return {
        "id": ACTION,
        "summary": "Review circuits",
        "location": "Library room 4",
        "description": "Added by NavoX",
        "start": {"dateTime": start.isoformat(), "timeZone": "America/New_York"},
        "end": {
            "dateTime": (start + timedelta(minutes=30)).isoformat(),
            "timeZone": "America/New_York",
        },
        "visibility": "private",
        "transparency": "opaque",
        "extendedProperties": {"private": {"navoxAction": ACTION, "navoxDigest": DIGEST}},
        "reminders": {"useDefault": False},
        **changes,
    }


class Wire:
    def __init__(self):
        self.requests = []
        self.calls = 0
        self.stored = None
        self.etag = '"v1"'
        self.put_etag = '"v1"'
        self.targets = multi(
            xml_response(HOME, "<d:resourcetype><d:collection/></d:resourcetype>"),
            collection(),
        )
        self.after_put = None
        self.after_get = None
        self.put_status = 201
        self.put_body = b""
        self.put_location = None
        self.put_error = False
        self.get_status = None
        self.get_etag = None
        self.get_body = None
        self.delete_status = None
        self.change_before_delete = False
        self.discovery_redirect = None
        self.discovery_change = None

    async def check(self):
        self.calls += 1

    def __call__(self, request):
        self.requests.append(request)
        assert request.headers["authorization"] == AUTH
        if request.method == "PROPFIND":
            if str(request.url) == apple.DISCOVERY_URL:
                if self.discovery_redirect:
                    return httpx.Response(307, headers={"Location": self.discovery_redirect})
                content = multi(
                    xml_response(
                        apple.DISCOVERY_URL,
                        f"<d:current-user-principal><d:href>{PRINCIPAL}</d:href>"
                        "</d:current-user-principal>",
                    )
                )
            elif str(request.url) == PRINCIPAL:
                content = multi(
                    xml_response(
                        PRINCIPAL,
                        f"<c:calendar-home-set><d:href>{HOME}</d:href></c:calendar-home-set>",
                    )
                )
            else:
                assert str(request.url) == HOME and request.headers["depth"] == "1"
                content = self.targets
            if self.discovery_change:
                content = self.discovery_change(request, content)
            return httpx.Response(207, content=content)
        assert str(request.url) == RESOURCE
        if request.method == "PUT":
            assert request.headers["if-none-match"] == "*"
            self.stored = bytes(request.content)
            if self.after_put:
                self.after_put(self)
            if self.put_error:
                raise httpx.ReadTimeout("synthetic timeout after write")
            headers = {}
            if self.put_etag is not None:
                headers["etag"] = self.put_etag
            if self.put_location is not None:
                headers["location"] = self.put_location
            return httpx.Response(self.put_status, content=self.put_body, headers=headers)
        if request.method == "GET":
            if self.after_get:
                self.after_get(self)
            status = self.get_status or (200 if self.stored is not None else 404)
            return httpx.Response(
                status,
                content=self.get_body or self.stored or b"",
                headers={
                    "Content-Type": "text/calendar; charset=utf-8",
                    "ETag": self.get_etag or self.etag,
                },
            )
        assert request.method == "DELETE"
        assert request.headers["if-match"] == '"v1"'
        if self.change_before_delete:
            self.etag = '"v2"'
        if self.delete_status:
            return httpx.Response(self.delete_status)
        if request.headers["if-match"] != self.etag:
            return httpx.Response(412)
        self.stored = None
        return httpx.Response(204)

    def provider(self, *, enabled=True, timezone="America/New_York"):
        return apple.AppleCalendarWriteProvider(
            transport=httpx.MockTransport(self), enabled=enabled, timezone=timezone
        )

    def count(self, method):
        return sum(request.method == method for request in self.requests)


async def insert(wire, event=None):
    return await wire.provider().insert(
        apple_id=ACCOUNT,
        app_password=PASSWORD,
        calendar_id=TARGET,
        event=event or reviewed(),
        check=wire.check,
    )


async def read(wire):
    return await wire.provider().event(
        apple_id=ACCOUNT,
        app_password=PASSWORD,
        calendar_id=TARGET,
        event_id=ACTION,
        check=wire.check,
    )


async def undo(wire, *, etag='"v1"'):
    return await wire.provider().delete(
        apple_id=ACCOUNT,
        app_password=PASSWORD,
        calendar_id=TARGET,
        event_id=ACTION,
        etag=etag,
        check=wire.check,
    )


@pytest.mark.asyncio
async def test_disabled_provider_never_writes_but_owned_target_reads_remain_available():
    wire, provider = Wire(), apple.AppleCalendarWriteProvider()
    with pytest.raises(CalendarWriteRejected, match="not enabled"):
        await provider.insert(
            apple_id=ACCOUNT,
            app_password=PASSWORD,
            calendar_id=TARGET,
            event=reviewed(),
            check=wire.check,
        )
    with pytest.raises(CalendarWriteRejected, match="not enabled"):
        await provider.delete(
            apple_id=ACCOUNT,
            app_password=PASSWORD,
            calendar_id=TARGET,
            event_id=ACTION,
            etag='"v1"',
            check=wire.check,
        )
    targets = await wire.provider(enabled=False).owned_calendars(
        apple_id=ACCOUNT, app_password=PASSWORD, check=wire.check
    )
    assert len(targets) == 1 and targets[0].id == TARGET
    assert wire.count("PUT") == wire.count("DELETE") == 0


@pytest.mark.asyncio
async def test_create_is_one_conditional_put_exact_private_readback_and_conditional_undo():
    wire = Wire()
    result = await insert(wire)
    assert result == CalendarCreateReceipt(
        {**reviewed(), "status": "confirmed", "etag": '"v1"'}, '"v1"'
    )
    assert wire.count("PUT") == wire.count("GET") == 1
    parsed = Calendar.from_ical(wire.stored)
    assert len(parsed.subcomponents) == 1
    item = parsed.subcomponents[0]
    assert item.name == "VEVENT" and not item.subcomponents
    assert str(item["CLASS"]) == "PRIVATE" and str(item["TRANSP"]) == "OPAQUE"
    assert str(item["DESCRIPTION"]) == "Added by NavoX"
    assert str(item["UID"]) == "navox-" + ACTION + "@navox.app"
    assert not {"ATTENDEE", "ORGANIZER", "RRULE", "ATTACH"}.intersection(item)
    assert "METHOD" not in parsed
    assert wire.calls == len(wire.requests) * 2
    await undo(wire)
    assert wire.count("DELETE") == 1 and await read(wire) is None
    assert wire.calls == len(wire.requests) * 2


@pytest.mark.asyncio
async def test_unicode_folding_and_each_utc_dst_fold_roundtrip_without_time_guessing():
    for hour in (5, 6):
        wire = Wire()
        event = reviewed(summary="Étude; " * 24, location="Bibliothèque, deuxième étage")
        start = datetime(2030, 11, 3, hour, 10, tzinfo=UTC)
        event["start"]["dateTime"] = start.isoformat()
        event["end"]["dateTime"] = (start + timedelta(minutes=30)).isoformat()
        result = await insert(wire, event)
        assert {key: result.event[key] for key in event} == event
        assert result.creation_etag == '"v1"'
        assert b"\r\n " in wire.stored
        assert Calendar.from_ical(wire.stored).subcomponents[0].decoded("DTSTART") == start


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "privileges,expected",
    [
        (("read", "write"), 1),
        (("all",), 1),
        (("read",), 0),
        (("write",), 0),
    ],
)
async def test_actual_collection_privileges_are_required(privileges, expected):
    wire = Wire()
    wire.targets = multi(collection(privileges=privileges))
    targets = await wire.provider().owned_calendars(
        apple_id=ACCOUNT, app_password=PASSWORD, check=wire.check
    )
    assert len(targets) == expected


@pytest.mark.asyncio
async def test_shared_owner_is_not_a_writable_personal_target():
    wire = Wire()
    wire.targets = multi(collection(owner="https://p42-caldav.icloud.com/other/principal/"))
    with pytest.raises(CalendarWriteRejected):
        await insert(wire)
    assert wire.count("PUT") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw.replace(b"<d:owner>", b"<d:missing>"),
        lambda raw: raw.replace(b"<d:status>HTTP/1.1 200 OK", b"<d:status>HTTP/1.1 404 Missing"),
        lambda raw: raw.replace(
            b"<d:response>", b"<d:response><d:status>HTTP/1.1 507 Limit</d:status>"
        ),
        lambda raw: multi(collection(), collection()),
        lambda raw: b'<!DOCTYPE test [<!ENTITY private "secret">]>' + raw,
    ],
)
async def test_partial_duplicate_or_unsafe_discovery_never_produces_a_write(change):
    wire = Wire()
    wire.targets = change(wire.targets)
    with pytest.raises(GoogleCalendarWriteError):
        await insert(wire)
    assert wire.count("PUT") == 0


@pytest.mark.parametrize(
    "extra",
    [
        "<d:prop><d:owner><d:href>/other/</d:href></d:owner></d:prop>",
        "<d:status>HTTP/1.1 200 OK</d:status>",
        "<d:status>HTTP/1.1 403 Forbidden</d:status>",
    ],
)
def test_propstat_requires_exactly_one_property_container_and_success_status(extra):
    row = xml_response(TARGET, "<d:owner><d:href>/owner/</d:href></d:owner>")
    content = multi(row.replace("</d:propstat>", extra + "</d:propstat>"))
    with pytest.raises(_Unavailable):
        _properties(_xml(content).find(DAV + "response"))


def test_disjoint_successful_propstats_preserve_properties_but_duplicate_owner_is_ambiguous():
    row = xml_response(TARGET, "<d:owner><d:href>/owner/</d:href></d:owner>")
    second = (
        "<d:propstat><d:prop><d:displayname>Personal</d:displayname></d:prop>"
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
    )
    content = multi(row.replace("</d:response>", second + "</d:response>"))
    props = _properties(_xml(content).find(DAV + "response"))
    assert set(props) == {DAV + "owner", DAV + "displayname"}
    duplicate = second.replace(
        "<d:displayname>Personal</d:displayname>",
        "<d:owner><d:href>/other/</d:href></d:owner>",
    )
    content = multi(row.replace("</d:response>", duplicate + "</d:response>"))
    with pytest.raises(_Unavailable):
        _properties(_xml(content).find(DAV + "response"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [apple.DISCOVERY_URL, PRINCIPAL, HOME])
@pytest.mark.parametrize("duplicate", ["prop", "status", "failed-status"])
async def test_ambiguous_principal_home_or_target_propstat_never_reaches_put(stage, duplicate):
    wire = Wire()

    def change(request, raw):
        if str(request.url) != stage:
            return raw
        extra = {
            "prop": b"<d:prop><d:owner><d:href>/other/</d:href></d:owner></d:prop>",
            "status": b"<d:status>HTTP/1.1 200 OK</d:status>",
            "failed-status": b"<d:status>HTTP/1.1 403 Forbidden</d:status>",
        }[duplicate]
        return raw.replace(b"</d:propstat>", extra + b"</d:propstat>", 1)

    wire.discovery_change = change
    with pytest.raises(GoogleCalendarWriteError):
        await insert(wire)
    assert wire.count("PUT") == wire.count("DELETE") == 0
    assert wire.count("PROPFIND") == [apple.DISCOVERY_URL, PRINCIPAL, HOME].index(stage) + 1


@pytest.mark.asyncio
async def test_separate_complete_target_propstats_remain_writable():
    wire = Wire()
    wire.targets = wire.targets.replace(
        b"<d:displayname>",
        b"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
        b"<d:propstat><d:prop><d:displayname>",
    )
    assert (await insert(wire)).creation_etag == '"v1"'
    assert wire.count("PUT") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/personal/",
        "http://p42-caldav.icloud.com/personal/",
        "https://p42-caldav.icloud.com:444/personal/",
        "https://p42-caldav.icloud.com/%2e%2e/",
        TARGET.replace("personal", "per%73onal"),
        TARGET.replace("personal", "per%ZZonal"),
        TARGET.replace("personal", "per%252fonal"),
        TARGET.replace("personal", "per%255conal"),
    ],
)
async def test_untrusted_selected_calendar_never_receives_credentials(url):
    wire = Wire()
    with pytest.raises(GoogleCalendarWriteError):
        await wire.provider().target(
            apple_id=ACCOUNT, app_password=PASSWORD, calendar_id=url, check=wire.check
        )
    assert wire.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [apple.DISCOVERY_URL, PRINCIPAL, HOME])
@pytest.mark.parametrize("alias", ["%31", "%ZZ", "%252f", "%255c"])
async def test_noncanonical_discovery_hrefs_never_produce_a_write(stage, alias):
    wire = Wire()

    def change(request, raw):
        if str(request.url) != stage:
            return raw
        original = {apple.DISCOVERY_URL: PRINCIPAL, PRINCIPAL: HOME, HOME: TARGET}[stage]
        # Principal/home use a numeric owner prefix; target uses a calendar name.
        aliased = original.replace("123/", alias + "23/", 1)
        return raw.replace(original.encode(), aliased.encode())

    wire.discovery_change = change
    with pytest.raises(GoogleCalendarWriteError):
        await insert(wire)
    assert wire.count("PUT") == wire.count("DELETE") == 0
    assert wire.count("PROPFIND") == [apple.DISCOVERY_URL, PRINCIPAL, HOME].index(stage) + 1


def test_trusted_uri_accepts_one_canonical_utf8_spelling_and_rejects_aliases():
    canonical = HOME + "caf%C3%A9/"
    assert apple._trusted(HOME, canonical) == canonical
    for value in (HOME + "café/", HOME + "caf%c3%a9/", HOME + "personal%2f"):
        with pytest.raises(GoogleCalendarWriteError):
            apple._trusted(HOME, value)


@pytest.mark.asyncio
async def test_discovery_redirect_cannot_forward_credentials_to_an_untrusted_host():
    wire = Wire()
    wire.discovery_redirect = "https://icloud.com.attacker.example/principal/"
    with pytest.raises(GoogleCalendarWriteError):
        await insert(wire)
    assert len(wire.requests) == 1 and wire.requests[0].url.host == "caldav.icloud.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"attendees": []},
        {"recurrence": []},
        {"conferenceData": {}},
        {"description": ""},
        {"visibility": "public"},
        {"reminders": {"useDefault": True}},
        {"id": "other-event"},
        {
            "extendedProperties": {
                "private": {"navoxAction": ACTION, "navoxDigest": DIGEST},
                "shared": {"other": "value"},
            }
        },
        {"summary": "private\ninstruction"},
        {"start": {"dateTime": "2030-11-03T05:10:00.001+00:00", "timeZone": "America/New_York"}},
    ],
)
async def test_unreviewed_or_unsupported_fields_are_rejected_before_http(changes):
    wire = Wire()
    with pytest.raises(CalendarWriteRejected):
        await insert(wire, reviewed(**changes))
    assert wire.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        b"ATTENDEE:mailto:other@example.test\r\n",
        b"ORGANIZER:mailto:other@example.test\r\n",
        b"RRULE:FREQ=DAILY\r\n",
        b"ATTACH:https://example.test/private\r\n",
        b"SUMMARY:duplicate\r\n",
        b"X-UNKNOWN:unreviewed\r\n",
        b"BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT5M\r\nEND:VALARM\r\n",
    ],
)
async def test_modified_invitation_or_unknown_content_never_reaches_delete(extra):
    wire = Wire()
    await insert(wire)
    wire.stored = wire.stored.replace(b"END:VEVENT", extra + b"END:VEVENT")
    with pytest.raises(GoogleCalendarWriteError):
        await undo(wire)
    assert wire.count("DELETE") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("weak", ['W/"v1"', "*", '"v1", "v2"', "missing"])
async def test_undo_requires_one_specific_strong_etag_before_any_request(weak):
    wire = Wire()
    with pytest.raises(CalendarWriteRejected):
        await undo(wire, etag=weak)
    assert wire.requests == []


@pytest.mark.asyncio
async def test_stale_version_before_or_during_delete_does_not_remove_changed_event():
    for during in (False, True):
        wire = Wire()
        await insert(wire)
        if during:
            wire.change_before_delete = True
        else:
            wire.etag = '"v2"'
        with pytest.raises(CalendarWriteChanged):
            await undo(wire)
        assert wire.stored is not None and wire.count("DELETE") == int(during)


@pytest.mark.asyncio
async def test_write_timeout_after_effect_is_uncertain_and_never_replayed():
    wire = Wire()
    wire.put_error = True
    with pytest.raises(CalendarWriteUncertain):
        await insert(wire)
    assert wire.count("PUT") == 1 and wire.count("GET") == 0 and wire.stored is not None
    assert (await read(wire))["id"] == ACTION
    assert wire.count("PUT") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"put_status": 200},
        {"put_status": 412},
        {"put_status": 409},
        {"put_status": 307},
        {"put_body": b"unexpected"},
        {"put_location": "https://attacker.example/event.ics"},
        {"get_status": 404},
        {"get_etag": 'W/"weak"'},
        {"get_etag": '"v2"'},
        {"put_etag": "W/malformed"},
    ],
)
async def test_unconfirmed_create_receipts_remain_uncertain(changes):
    wire = Wire()
    for key, value in changes.items():
        setattr(wire, key, value)
    with pytest.raises(CalendarWriteUncertain):
        await insert(wire)
    assert wire.count("PUT") == 1
    assert all(
        request.url.host in {"caldav.icloud.com", "p42-caldav.icloud.com"}
        for request in wire.requests
    )


@pytest.mark.asyncio
async def test_normalized_storage_requires_get_proof_and_never_trusts_inconsistent_put_etag():
    for put_etag, allowed in ((None, True), ('W/"normalized"', True), ('"v1"', False)):
        wire = Wire()
        wire.put_etag = put_etag
        wire.after_put = lambda value: setattr(
            value, "stored", value.stored.replace(b"\r\n", b"\n")
        )
        if allowed:
            result = await insert(wire)
            assert result.event["etag"] == '"v1"' and result.creation_etag is None
        else:
            with pytest.raises(CalendarWriteUncertain):
                await insert(wire)
        assert wire.count("PUT") == wire.count("GET") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("put_etag", [None, 'W/"normalized"'])
async def test_later_matching_get_version_is_observed_without_promoting_creation_proof(put_etag):
    wire = Wire()
    wire.put_etag = put_etag

    def later_edit(value):
        value.stored = value.stored.replace(
            b"END:VEVENT", b"SEQUENCE:2\r\nLAST-MODIFIED:20301103T050001Z\r\nEND:VEVENT"
        )
        value.etag = '"v2"'

    wire.after_get = later_edit
    receipt = await insert(wire)
    assert receipt.event == {**reviewed(), "status": "confirmed", "etag": '"v2"'}
    assert receipt.creation_etag is None
    assert wire.count("PUT") == wire.count("GET") == 1 and wire.count("DELETE") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["put", "get"])
async def test_lost_native_authority_always_propagates_without_a_second_write(when):
    wire = Wire()
    revoked = [False]

    async def check():
        if revoked[0]:
            raise StudentError(409, "synthetic owner changed")

    setattr(wire, "after_" + when, lambda value: revoked.__setitem__(0, True))
    with pytest.raises(StudentError):
        await wire.provider().insert(
            apple_id=ACCOUNT,
            app_password=PASSWORD,
            calendar_id=TARGET,
            event=reviewed(),
            check=check,
        )
    assert wire.count("PUT") == 1 and wire.count("GET") == int(when == "get")


@pytest.mark.asyncio
async def test_provider_response_limit_is_enforced_before_parsing(monkeypatch):
    wire = Wire()
    monkeypatch.setattr(apple, "MAX_RESPONSE_BYTES", 12)
    with pytest.raises(GoogleCalendarWriteError):
        await insert(wire)
    assert wire.count("PUT") == 0


@pytest.mark.asyncio
async def test_read_never_fabricates_absence_on_server_error_or_redirect():
    for status in (301, 401, 403, 410, 429, 500, 503):
        wire = Wire()
        wire.get_status = status
        with pytest.raises(GoogleCalendarWriteError):
            await read(wire)
        assert wire.count("GET") == 1 and wire.count("PUT") == wire.count("DELETE") == 0


@pytest.mark.asyncio
async def test_unknown_deleted_response_is_held_rather_than_guessed_absent():
    wire = Wire()
    await insert(wire)
    wire.delete_status = 404
    with pytest.raises(CalendarWriteUncertain):
        await undo(wire)
    assert wire.count("DELETE") == 1
    assert wire.stored is not None


@pytest.mark.asyncio
async def test_changed_personal_fields_are_observed_for_native_receipt_comparison():
    wire = Wire()
    await insert(wire)
    wire.stored = wire.stored.replace(b"Review circuits", b"Edited subject")
    wire.etag = '"v2"'
    result = await read(wire)
    assert result["summary"] == "Edited subject" and result["etag"] == '"v2"'
    assert result["extendedProperties"] == reviewed()["extendedProperties"]
    assert wire.count("PUT") == 1 and wire.count("DELETE") == 0


def test_request_validation_does_not_mutate_the_native_review():
    event = reviewed()
    original = deepcopy(event)
    assert apple._input(event) == event and event == original
