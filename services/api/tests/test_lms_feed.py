"""Synthetic calendars only. These tests are not multi-school student validation."""

import asyncio
import gzip
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.api import auth, lms_feed, student
from navox.api import student_planning as planning_api
from navox.connectors.builtin.lms_feed import parse_feed
from navox.core.settings import Settings, get_settings
from navox.db import student_planning  # noqa: F401 - register source-copy cleanup metadata
from navox.db.lms_feeds import LmsFeed, LmsFeedItem, LmsFeedPreview, LmsFeedSource
from navox.db.models import ConnectionCredential, ConnectorConnection, User, WorkspaceMembership
from navox.db.session import get_database_session
from navox.lms_feeds.activities import sync_feed
from navox.lms_feeds.schemas import ConfirmCommand
from navox.lms_feeds.service import FeedAccessError, confirm_preview, owned_item
from navox.lms_feeds.transport import (
    MAX_FEED_BYTES,
    FeedError,
    FeedFetcher,
    FeedResponse,
    feed_domain,
)

PRIVATE_URL = "https://canvas.school.example/feeds/private.ics?token=synthetic-private-key"
NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


def calendar(*events: str, product: str = "Canvas") -> bytes:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//"
        + product
        + "//EN\r\n"
        + "\r\n".join("BEGIN:VEVENT\r\n" + event + "\r\nEND:VEVENT" for event in events)
        + "\r\nEND:VCALENDAR\r\n"
    ).encode()


def event(uid: str = "assignment-1", title: str = "[CS 2110] Homework", *, days: int = 1) -> str:
    when = (datetime.now(UTC) + timedelta(days=days)).strftime("%Y%m%dT%H%M%SZ")
    return f"UID:{uid}\r\nSUMMARY:{title}\r\nDTSTART:{when}"


def parse(content: bytes, **kwargs):
    return parse_feed(
        content,
        timezone="America/New_York",
        all_day_due_time="23:59",
        now=NOW,
        private_url=PRIVATE_URL,
        **kwargs,
    )


def test_all_day_course_html_source_links_and_platform():
    result = parse(
        calendar(
            "UID:a\r\nSUMMARY:[CS 2110] Homework 1\r\nDTSTART;VALUE=DATE:20261020\r\n"
            'DESCRIPTION:<p>Read <a href="https://school.example/a?token=hidden">notes</a></p><script>bad()</script>\r\n'
            "URL:https://school.example/assignment/1?auth=private\r\nLOCATION:Library"
        )
    )
    item = result.items[0]
    assert result.platform == "canvas"
    assert result.course_hints == ["CS 2110"]
    assert item.title == "Homework 1" and item.kind == "assignment"
    assert item.due_at == datetime(2026, 10, 21, 3, 59, tzinfo=UTC)
    assert item.details == "Read https://school.example/a notes"
    assert item.source_url == "https://school.example/assignment/1"
    assert item.location == "Library" and result.all_day_count == 1
    assert "hidden" not in result.model_dump_json() and "bad()" not in item.details


def test_dst_recurrence_exception_rdate_exdate_stable_identity():
    result = parse(
        calendar(
            "UID:weekly\r\nSUMMARY:ECE 2300 lecture\r\n"
            "DTSTART;TZID=America/New_York:20261025T090000\r\n"
            "DTEND;TZID=America/New_York:20261025T100000\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\n"
            "RDATE;TZID=America/New_York:20261109T090000\r\nEXDATE;TZID=America/New_York:20261108T090000",
            "UID:weekly\r\nRECURRENCE-ID;TZID=America/New_York:20261101T090000\r\n"
            "SUMMARY:ECE 2300 moved\r\nDTSTART;TZID=America/New_York:20261101T110000\r\n"
            "DTEND;TZID=America/New_York:20261101T120000",
        )
    )
    assert [item.starts_at.hour for item in result.items] == [13, 16, 14]
    assert result.items[1].occurrence_id == "2026-11-01T14:00:00+00:00"
    assert [item.starts_at.day for item in result.items] == [25, 1, 9]


@pytest.mark.parametrize(
    "content",
    [
        b"not a calendar",
        calendar("UID:a\r\nSUMMARY:A"),
        calendar("UID:a\r\nSUMMARY:A\r\nDTSTART:20261020T120000Z\r\nRRULE:FREQ=SECONDLY"),
        calendar("UID:a\r\nSUMMARY:A\r\nDTSTART;TZID=Invented/Zone:20261020T120000"),
        calendar(
            "UID:a\r\nSUMMARY:A\r\nDTSTART:20261020T120000Z",
            "UID:a\r\nSUMMARY:B\r\nDTSTART:20261020T130000Z",
        ),
    ],
)
def test_malformed_or_excessive_recurrence_refuses_whole_snapshot(content):
    with pytest.raises(FeedError):
        parse(content)


def test_1000_events_and_bounded_recurrence():
    assert (
        len(
            parse(
                calendar(
                    *(
                        f"UID:{i}\r\nSUMMARY:Due {i}\r\nDTSTART:20261020T120000Z"
                        for i in range(1_000)
                    )
                )
            ).items
        )
        == 1_000
    )
    with pytest.raises(FeedError):
        parse(
            calendar(
                *(f"UID:{i}\r\nSUMMARY:Due {i}\r\nDTSTART:20261020T120000Z" for i in range(1_001))
            )
        )
    with pytest.raises(FeedError):
        parse(
            calendar(
                *(
                    f"UID:{i}\r\nSUMMARY:A\r\nDTSTART:20261020T120000Z\r\nRRULE:FREQ=DAILY"
                    for i in range(20)
                )
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://school.example/a.ics",
        "https://127.0.0.1/a.ics",
        "https://[::1]/a.ics",
        "https://localhost/a.ics",
        "https://x.local/a.ics",
        "https://user:pass@school.example/a",
        "https://school.example:8443/a",
        "https://school.example/a#fragment",
        "https://school.example/%0d%0aX",
    ],
)
def test_url_validation_rejects_unsafe_targets(url):
    with pytest.raises(FeedError):
        feed_domain(url)


@pytest.mark.asyncio
async def test_transport_pins_public_dns_preserves_host_sni_secret_query_and_validators(caplog):
    requests = []

    async def resolve(host):
        assert host == "canvas.school.example"
        return ["93.184.216.34"]

    def send(request):
        requests.append(request)
        return httpx.Response(304, headers={"ETag": '"new"'})

    fetcher = FeedFetcher(resolver=resolve, transport=httpx.MockTransport(send))
    result = await fetcher.fetch(
        PRIVATE_URL, etag='"old"', last_modified="Fri, 16 Oct 2026 12:00:00 GMT"
    )
    assert result.status == 304 and result.etag == '"new"'
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["Host"] == "canvas.school.example"
    assert requests[0].extensions["sni_hostname"] == "canvas.school.example"
    assert requests[0].url.params["token"] == "synthetic-private-key"
    assert requests[0].headers["If-None-Match"] == '"old"'
    assert "authorization" not in requests[0].headers and "cookie" not in requests[0].headers
    assert "synthetic-private-key" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["93.184.216.34", "10.0.0.1"], ["::ffff:127.0.0.1"], ["169.254.169.254"]],
)
async def test_dns_private_mixed_and_rebinding_targets_blocked(addresses):
    async def resolve(host):
        return addresses

    def forbidden(request):
        pytest.fail("A forbidden address reached the transport")

    with pytest.raises(FeedError):
        await FeedFetcher(resolver=resolve, transport=httpx.MockTransport(forbidden)).fetch(
            PRIVATE_URL
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://evil.example/steal"}),
        httpx.Response(200, headers={"Content-Length": str(MAX_FEED_BYTES + 1)}),
        httpx.Response(200, content=b"x" * (MAX_FEED_BYTES + 1)),
        httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(b"data")),
    ],
)
async def test_redirect_compression_and_oversize_are_refused(response):
    async def resolve(host):
        return ["93.184.216.34"]

    with pytest.raises(FeedError):
        await FeedFetcher(
            resolver=resolve, transport=httpx.MockTransport(lambda request: response)
        ).fetch(PRIVATE_URL)


class FakeFetcher:
    def __init__(self):
        self.content = calendar(event())
        self.status = 200
        self.calls = 0
        self.hook = None
        self.last_headers = None

    async def fetch(self, url, **kwargs):
        self.calls += 1
        self.last_headers = kwargs
        assert url == PRIVATE_URL
        if self.hook:
            await self.hook()
        if self.status >= 400:
            raise FeedError("feed_http_error", status=self.status)
        return FeedResponse(self.status, self.content if self.status == 200 else b"", '"fixture"')


@pytest_asyncio.fixture
async def feed_env(ai_database) -> AsyncIterator[SimpleNamespace]:
    factory = ai_database
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(lms_feed.router, prefix="/api/v1")
    app.include_router(planning_api.router, prefix="/api/v1")
    app.include_router(student.router, prefix="/api/v1")
    settings = Settings(
        _env_file=None,
        app_environment="test",
        connector_secret_encryption_key=Fernet.generate_key().decode(),
    )
    object.__setattr__(settings, "student_lms_feeds_enabled", True)
    object.__setattr__(settings, "student_planning_enabled", True)
    fetcher = FakeFetcher()

    async def database_override() -> AsyncIterator[AsyncSession]:
        async with factory() as database:
            yield database

    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[lms_feed.get_feed_fetcher] = lambda: fetcher
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": settings.web_origin},
    ) as client:
        account = (
            await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "feeds@example.com",
                    "password": "twelve-character-password",
                    "display_name": "Student",
                },
            )
        ).json()
        yield SimpleNamespace(
            client=client,
            factory=factory,
            app=app,
            settings=settings,
            fetcher=fetcher,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
        )


async def connect(env):
    response = await env.client.post(
        "/api/v1/student/lms-feeds/preview",
        json={
            "url": PRIVATE_URL,
            "platform": "canvas",
            "timezone": "America/New_York",
            "all_day_due_time": "23:59",
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    command = {
        "preview_token": preview["preview_token"],
        "preview_hash": preview["preview_hash"],
        "request_id": str(uuid4()),
        "name": "My courses",
        "course_matches": {hint: None for hint in preview["course_hints"]},
        "confirmed": True,
    }
    result = await env.client.post("/api/v1/student/lms-feeds", json=command)
    assert result.status_code == 201, result.text
    return result.json(), preview, command


@pytest.mark.asyncio
async def test_encrypted_preview_confirm_idempotent_uid_and_domain_only(feed_env):
    feed, preview, command = await connect(feed_env)
    assert "synthetic-private-key" not in json.dumps([feed, preview])
    again = await feed_env.client.post("/api/v1/student/lms-feeds", json=command)
    assert again.status_code == 201 and again.json()["id"] == feed["id"]
    async with feed_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(LmsFeedItem)) == 1
        credential = await database.scalar(select(ConnectionCredential))
        assert "synthetic-private-key" not in credential.encrypted_refresh_token
        assert (await database.get(LmsFeedPreview, UUID(preview["preview_token"]))).parsed == {}


@pytest.mark.asyncio
async def test_diff_rename_deadline_cancel_and_cross_feed_dedup_then_forget(feed_env):
    first, _, _ = await connect(feed_env)
    second, _, _ = await connect(feed_env)
    async with feed_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(LmsFeedItem)) == 1
        assert await database.scalar(select(func.count()).select_from(LmsFeedSource)) == 2
    feed_env.fetcher.content = calendar(event(title="[CS 2110] Renamed assignment", days=2))
    result = await sync_feed(
        feed_env.factory,
        feed_id=UUID(first["id"]),
        user_id=feed_env.user_id,
        workspace_id=feed_env.workspace_id,
        settings=feed_env.settings,
        fetcher=feed_env.fetcher,
        now=datetime.now(UTC) + timedelta(hours=3),
    )
    assert result.updated == 1
    async with feed_env.factory() as database:
        item = await database.scalar(select(LmsFeedItem))
        assert item.conflicted
    assert (
        await feed_env.client.delete(f"/api/v1/student/lms-feeds/{second['id']}")
    ).status_code == 204
    async with feed_env.factory() as database:
        item = await database.scalar(select(LmsFeedItem))
        assert not item.conflicted and item.payload["title"] == "Renamed assignment"
    feed_env.fetcher.content = calendar()
    result = await sync_feed(
        feed_env.factory,
        feed_id=UUID(first["id"]),
        user_id=feed_env.user_id,
        workspace_id=feed_env.workspace_id,
        settings=feed_env.settings,
        fetcher=feed_env.fetcher,
        now=datetime.now(UTC) + timedelta(hours=6),
    )
    assert result.cancelled == 1
    async with feed_env.factory() as database:
        assert (await database.scalar(select(LmsFeedItem))).cancelled
    assert (
        await feed_env.client.delete(f"/api/v1/student/lms-feeds/{first['id']}")
    ).status_code == 204
    async with feed_env.factory() as database:
        for model in (LmsFeed, LmsFeedSource, LmsFeedItem, LmsFeedPreview, ConnectionCredential):
            assert await database.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_304_and_near_deadline_schedule_and_three_invalid_links_pause(feed_env):
    feed, _, _ = await connect(feed_env)
    feed_env.fetcher.status = 304
    now = datetime.now(UTC)
    result = await sync_feed(
        feed_env.factory,
        feed_id=UUID(feed["id"]),
        user_id=feed_env.user_id,
        workspace_id=feed_env.workspace_id,
        settings=feed_env.settings,
        fetcher=feed_env.fetcher,
        now=now,
    )
    assert result.state == "unchanged"
    async with feed_env.factory() as database:
        row = await database.get(LmsFeed, UUID(feed["id"]))
        assert abs((row.next_sync_at.replace(tzinfo=UTC) - now).total_seconds() - 1800) < 1
    feed_env.fetcher.status = 403
    for index in range(3):
        result = await sync_feed(
            feed_env.factory,
            feed_id=UUID(feed["id"]),
            user_id=feed_env.user_id,
            workspace_id=feed_env.workspace_id,
            settings=feed_env.settings,
            fetcher=feed_env.fetcher,
            now=now + timedelta(hours=3 * (index + 1)),
        )
    assert result.state == "paused"
    assert (
        await feed_env.client.post(f"/api/v1/student/lms-feeds/{feed['id']}/refresh")
    ).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["pause", "delete", "revoke", "rotate", "demote", "pause_resume"]
)
async def test_inflight_privacy_change_wins_without_resurrection(feed_env, change):
    feed, _, _ = await connect(feed_env)
    feed_env.fetcher.content = calendar(event(title="Changed while reading"))

    async def mutate():
        if change == "delete":
            assert (
                await feed_env.client.delete(f"/api/v1/student/lms-feeds/{feed['id']}")
            ).status_code == 204
        else:
            async with feed_env.factory() as database:
                if change == "pause":
                    (await database.get(User, feed_env.user_id)).agent_paused = True
                elif change == "demote":
                    (
                        await database.get(
                            WorkspaceMembership, (feed_env.workspace_id, feed_env.user_id)
                        )
                    ).role = "viewer"
                else:
                    row = await database.get(LmsFeed, UUID(feed["id"]))
                    connection = await database.get(ConnectorConnection, row.connection_id)
                    if change == "revoke":
                        connection.authorized_capabilities = []
                    elif change == "pause_resume":
                        from navox.connectors.management import transition_connection

                        for operation in ("pause", "resume"):
                            await transition_connection(
                                database,
                                user_id=feed_env.user_id,
                                workspace_id=feed_env.workspace_id,
                                connection_id=connection.id,
                                operation=operation,
                                request_id=uuid4(),
                            )
                    else:
                        from navox.connectors.secrets import SecretBroker

                        await SecretBroker(feed_env.settings).store(
                            database,
                            {"FEED_URL": PRIVATE_URL},
                            connection_id=connection.id,
                            user_id=feed_env.user_id,
                            workspace_id=feed_env.workspace_id,
                        )
                await database.commit()

    feed_env.fetcher.hook = mutate
    result = await sync_feed(
        feed_env.factory,
        feed_id=UUID(feed["id"]),
        user_id=feed_env.user_id,
        workspace_id=feed_env.workspace_id,
        settings=feed_env.settings,
        fetcher=feed_env.fetcher,
        now=datetime.now(UTC) + timedelta(hours=3),
    )
    assert result.state == "superseded"
    async with feed_env.factory() as database:
        rows = list(await database.scalars(select(LmsFeedItem)))
        assert all(row.payload["title"] != "Changed while reading" for row in rows)
        if change == "delete":
            assert rows == []


@pytest.mark.asyncio
async def test_foreign_owner_and_bad_confirmation_never_publish(feed_env):
    _, preview, command = await connect(feed_env)
    async with feed_env.factory() as database:
        with pytest.raises(FeedAccessError):
            await confirm_preview(
                database,
                user_id=uuid4(),
                workspace_id=feed_env.workspace_id,
                command=ConfirmCommand.model_validate(command),
                now=datetime.now(UTC),
            )
        item = await database.scalar(select(LmsFeedItem))
        with pytest.raises(FeedAccessError):
            await owned_item(
                database, user_id=feed_env.user_id, workspace_id=uuid4(), item_id=item.id
            )
    command["request_id"] = str(uuid4())
    assert (
        await feed_env.client.post("/api/v1/student/lms-feeds", json=command)
    ).status_code == 409
    bad = await feed_env.client.post(
        "/api/v1/student/lms-feeds/preview", json={"url": PRIVATE_URL, "timezone": "bad"}
    )
    assert bad.status_code == 422 and "synthetic-private-key" not in bad.text


@pytest.mark.asyncio
async def test_disabled_flag_blocks_reads_and_network_but_allows_forget(feed_env):
    feed, _, _ = await connect(feed_env)
    calls = feed_env.fetcher.calls
    object.__setattr__(feed_env.settings, "student_lms_feeds_enabled", False)
    assert (await feed_env.client.get("/api/v1/student/lms-feeds")).status_code == 404
    result = await sync_feed(
        feed_env.factory,
        feed_id=UUID(feed["id"]),
        user_id=feed_env.user_id,
        workspace_id=feed_env.workspace_id,
        settings=feed_env.settings,
        fetcher=feed_env.fetcher,
    )
    assert result.state == "disabled" and feed_env.fetcher.calls == calls
    assert (
        await feed_env.client.delete(f"/api/v1/student/lms-feeds/{feed['id']}")
    ).status_code == 204
    assert feed_env.fetcher.calls == calls
    async with feed_env.factory() as database:
        for model in (
            LmsFeed,
            LmsFeedPreview,
            LmsFeedItem,
            LmsFeedSource,
            ConnectorConnection,
            ConnectionCredential,
        ):
            assert await database.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_pause_resume_and_revision_protect_pending_refresh(feed_env):
    feed, _, _ = await connect(feed_env)
    paused = await feed_env.client.put(
        f"/api/v1/student/lms-feeds/{feed['id']}",
        json={"revision": feed["revision"], "enabled": False},
    )
    assert paused.status_code == 200 and paused.json()["state"] == "paused"
    assert (
        await feed_env.client.post(f"/api/v1/student/lms-feeds/{feed['id']}/refresh")
    ).status_code == 409
    stale = await feed_env.client.put(
        f"/api/v1/student/lms-feeds/{feed['id']}",
        json={"revision": feed["revision"], "enabled": True},
    )
    assert stale.status_code == 409
    resumed = await feed_env.client.put(
        f"/api/v1/student/lms-feeds/{feed['id']}",
        json={"revision": paused.json()["revision"], "enabled": True},
    )
    assert resumed.status_code == 200 and resumed.json()["state"] == "active"


@pytest.mark.asyncio
async def test_course_confirmation_requires_all_hints_and_owned_active_courses(feed_env):
    preview = (
        await feed_env.client.post(
            "/api/v1/student/lms-feeds/preview",
            json={
                "url": PRIVATE_URL,
                "platform": "canvas",
                "timezone": "UTC",
                "all_day_due_time": "23:59",
            },
        )
    ).json()
    body = {
        "preview_token": preview["preview_token"],
        "preview_hash": preview["preview_hash"],
        "request_id": str(uuid4()),
        "name": "Courses",
        "course_matches": {},
        "confirmed": True,
    }
    assert (await feed_env.client.post("/api/v1/student/lms-feeds", json=body)).status_code == 422
    body["course_matches"] = {preview["course_hints"][0]: str(uuid4())}
    assert (await feed_env.client.post("/api/v1/student/lms-feeds", json=body)).status_code == 422
    body["course_matches"] = {preview["course_hints"][0]: None}
    confirmed = await feed_env.client.post("/api/v1/student/lms-feeds", json=body)
    assert confirmed.status_code == 201
    current = await feed_env.client.get(
        f"/api/v1/student/lms-feeds/{confirmed.json()['id']}/matches"
    )
    assert current.status_code == 200
    assert current.json()["course_hints"] == preview["course_hints"]
    assert current.json()["unconfirmed_hints"] == []
    changed = await feed_env.client.put(
        f"/api/v1/student/lms-feeds/{confirmed.json()['id']}/matches",
        json={
            "revision": confirmed.json()["revision"],
            "course_matches": body["course_matches"],
            "confirmed": True,
        },
    )
    assert changed.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_membership", [False, True])
async def test_expired_preview_is_erased_even_when_network_rollout_disabled(
    feed_env, revoke_membership
):
    preview = (
        await feed_env.client.post(
            "/api/v1/student/lms-feeds/preview",
            json={
                "url": PRIVATE_URL,
                "platform": "canvas",
                "timezone": "UTC",
                "all_day_due_time": "23:59",
            },
        )
    ).json()
    object.__setattr__(feed_env.settings, "student_lms_feeds_enabled", False)
    if revoke_membership:
        async with feed_env.factory() as database:
            member = await database.get(
                WorkspaceMembership, (feed_env.workspace_id, feed_env.user_id)
            )
            await database.delete(member)
            await database.commit()
    from navox.lms_feeds.activities import reconcile_feeds

    await reconcile_feeds(
        feed_env.factory, settings=feed_env.settings, now=datetime.now(UTC) + timedelta(hours=1)
    )
    async with feed_env.factory() as database:
        assert await database.get(LmsFeedPreview, UUID(preview["preview_token"])) is None
        assert await database.scalar(select(func.count()).select_from(ConnectionCredential)) == 0


@pytest.mark.asyncio
async def test_forget_preserves_other_feed_then_erases_orphan_planner_copies(feed_env):
    first, _, _ = await connect(feed_env)
    second, _, _ = await connect(feed_env)
    snapshot = (await feed_env.client.get("/api/v1/student/lms-feeds/items")).json()["items"][0]
    imported = await feed_env.client.post(
        "/api/v1/student/planning/tasks/from-assignment",
        json={
            "title": "Feed-private coursework",
            "request_id": str(uuid4()),
            "estimated_minutes": 60,
            "source_item_id": snapshot["id"],
            "source_revision": snapshot["revision"],
            "confirmed": True,
            "due_at": snapshot["item"]["due_at"],
        },
    )
    assert imported.status_code == 201, imported.text
    manual = await feed_env.client.post(
        "/api/v1/student/planning/tasks",
        json={
            "title": "Independent manual task",
            "request_id": str(uuid4()),
            "estimated_minutes": 30,
        },
    )
    assert manual.status_code == 201, manual.text
    preview = await feed_env.client.post("/api/v1/student/planning/preview", json={"days": 2})
    assert preview.status_code == 200, preview.text
    reviewed = preview.json()
    accepted = await feed_env.client.post(
        "/api/v1/student/planning/accept",
        json={
            "preview_id": reviewed["preview_id"],
            "preview_hash": reviewed["preview_hash"],
            "request_id": str(uuid4()),
            "confirmed": True,
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert (
        await feed_env.client.delete(f"/api/v1/student/lms-feeds/{first['id']}")
    ).status_code == 204
    assert len((await feed_env.client.get("/api/v1/student/planning/tasks")).json()["tasks"]) == 2
    assert (
        await feed_env.client.delete(f"/api/v1/student/lms-feeds/{second['id']}")
    ).status_code == 204
    tasks = (await feed_env.client.get("/api/v1/student/planning/tasks")).json()["tasks"]
    assert [task["id"] for task in tasks] == [manual.json()["id"]]
    plan = (await feed_env.client.get("/api/v1/student/planning/plan")).json()
    assert "Feed-private coursework" not in json.dumps(plan)
    from navox.db.student_planning import StudentPlanningCommand, StudentPlanningPreview

    async with feed_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentPlanningPreview)) == 0
        receipts = list(await database.scalars(select(StudentPlanningCommand)))
        assert receipts and all(
            "Feed-private coursework" not in str(row.result) for row in receipts
        )
        assert all(row.undo_snapshot is None for row in receipts)


@pytest.mark.asyncio
async def test_postgres_concurrent_confirmation_replays_one_owned_snapshot(feed_env):
    if feed_env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Concurrent confirmation requires real PostgreSQL row locks")
    preview = (
        await feed_env.client.post(
            "/api/v1/student/lms-feeds/preview",
            json={
                "url": PRIVATE_URL,
                "platform": "canvas",
                "timezone": "UTC",
                "all_day_due_time": "23:59",
            },
        )
    ).json()
    command = ConfirmCommand(
        preview_token=preview["preview_token"],
        preview_hash=preview["preview_hash"],
        request_id=uuid4(),
        name="Courses",
        course_matches={hint: None for hint in preview["course_hints"]},
        confirmed=True,
    )

    async def confirm():
        async with feed_env.factory() as database:
            row = await confirm_preview(
                database,
                user_id=feed_env.user_id,
                workspace_id=feed_env.workspace_id,
                command=command,
                now=datetime.now(UTC),
            )
            await database.commit()
            return row.id

    first, second = await asyncio.gather(confirm(), confirm())
    assert first == second
    async with feed_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(LmsFeedItem)) == 1
        assert await database.scalar(select(func.count()).select_from(LmsFeedSource)) == 1


def test_private_feed_link_variants_uid_path_and_tokens_are_not_published():
    private = "https://lms.school.example/calendar/long-secret-path-token?secret=secret-query-value"
    variant = "https://LMS.SCHOOL.EXAMPLE/CALENDAR/LONG-SECRET-PATH-TOKEN?other=1&secret=secret-query-value"
    result = parse_feed(
        calendar(
            f"UID:{private}\r\nSUMMARY:Due soon\r\nDTSTART:20261020T120000Z\r\n"
            f'DESCRIPTION:<a href="{variant}">secret-query-value</a> long-secret-path-token\r\n'
            f"LOCATION:secret-query-value\r\nURL:{variant}"
        ),
        timezone="UTC",
        all_day_due_time="23:59",
        now=NOW,
        private_url=private,
    )
    published = result.model_dump_json()
    assert result.items[0].external_id.startswith("uid:")
    assert result.items[0].source_url is None
    assert "secret-query-value" not in published and "long-secret-path-token" not in published
    assert "LONG-SECRET-PATH-TOKEN" not in published and private not in published


@pytest.mark.asyncio
async def test_new_utc_day_refetches_body_even_when_etag_is_unchanged(feed_env):
    feed, _, _ = await connect(feed_env)
    now = datetime.now(UTC) + timedelta(days=1)
    result = await sync_feed(
        feed_env.factory,
        feed_id=UUID(feed["id"]),
        user_id=feed_env.user_id,
        workspace_id=feed_env.workspace_id,
        settings=feed_env.settings,
        fetcher=feed_env.fetcher,
        now=now,
    )
    assert result.state == "synced"
    assert feed_env.fetcher.last_headers == {"etag": None, "last_modified": None}
    async with feed_env.factory() as database:
        row = await database.get(LmsFeed, UUID(feed["id"]))
        assert row.last_expanded_at.replace(tzinfo=UTC) == now


@pytest.mark.asyncio
async def test_reset_during_preview_read_erases_old_feeds_and_blocks_stale_save(feed_env):
    await connect(feed_env)

    async def reset():
        response = await feed_env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
        )
        assert response.status_code == 200, response.text

    feed_env.fetcher.hook = reset
    payload = {
        "url": PRIVATE_URL,
        "platform": "canvas",
        "timezone": "UTC",
        "all_day_due_time": "23:59",
    }
    refused = await feed_env.client.post("/api/v1/student/lms-feeds/preview", json=payload)
    assert refused.status_code == 409 and "reset" in refused.text
    async with feed_env.factory() as database:
        for model in (
            LmsFeed,
            LmsFeedPreview,
            LmsFeedItem,
            LmsFeedSource,
            ConnectorConnection,
            ConnectionCredential,
        ):
            assert await database.scalar(select(func.count()).select_from(model)) == 0
    feed_env.fetcher.hook = None
    assert (
        await feed_env.client.post("/api/v1/student/lms-feeds/preview", json=payload)
    ).status_code == 200


@pytest.mark.asyncio
async def test_http_reset_erases_urls_tasks_plans_and_supersedes_inflight_sync(feed_env):
    from navox.db.student_planning import (
        StudentPlanningCommand,
        StudentPlanningPreview,
        StudentPlanningState,
        StudentStudyBlock,
        StudentTask,
    )

    feed, _, confirmation = await connect(feed_env)
    snapshot = (await feed_env.client.get("/api/v1/student/lms-feeds/items")).json()["items"][0]
    imported = await feed_env.client.post(
        "/api/v1/student/planning/tasks/from-assignment",
        json={
            "title": "Coursework erased by student reset",
            "request_id": str(uuid4()),
            "estimated_minutes": 60,
            "source_item_id": snapshot["id"],
            "source_revision": snapshot["revision"],
            "confirmed": True,
        },
    )
    assert imported.status_code == 201, imported.text
    manual = await feed_env.client.post(
        "/api/v1/student/planning/tasks",
        json={"title": "Manual reset task", "request_id": str(uuid4()), "estimated_minutes": 30},
    )
    assert manual.status_code == 201, manual.text
    preview = await feed_env.client.post("/api/v1/student/planning/preview", json={"days": 2})
    assert preview.status_code == 200, preview.text
    reviewed = preview.json()
    accepted = await feed_env.client.post(
        "/api/v1/student/planning/accept",
        json={
            "preview_id": reviewed["preview_id"],
            "preview_hash": reviewed["preview_hash"],
            "request_id": str(uuid4()),
            "confirmed": True,
        },
    )
    assert accepted.status_code == 200, accepted.text
    # Unconfirmed feed previews retain a separate encrypted subscription link.
    abandoned = await feed_env.client.post(
        "/api/v1/student/lms-feeds/preview",
        json={
            "url": PRIVATE_URL,
            "platform": "canvas",
            "timezone": "UTC",
            "all_day_due_time": "23:59",
        },
    )
    assert abandoned.status_code == 200, abandoned.text
    reset_command = {"confirmed": True, "revision": 0, "request_id": str(uuid4())}

    async def reset():
        response = await feed_env.client.post("/api/v1/student/data/reset", json=reset_command)
        assert response.status_code == 200 and response.json() == {"cleared": True, "reused": False}

    feed_env.fetcher.hook = reset
    feed_env.fetcher.content = calendar(event(title="Late provider result must not return"))
    result = await sync_feed(
        feed_env.factory,
        feed_id=UUID(feed["id"]),
        user_id=feed_env.user_id,
        workspace_id=feed_env.workspace_id,
        settings=feed_env.settings,
        fetcher=feed_env.fetcher,
        now=datetime.now(UTC) + timedelta(hours=3),
    )
    assert result.state == "superseded"
    async with feed_env.factory() as database:
        for model in (
            LmsFeed,
            LmsFeedPreview,
            LmsFeedItem,
            LmsFeedSource,
            ConnectorConnection,
            ConnectionCredential,
            StudentTask,
            StudentStudyBlock,
            StudentPlanningPreview,
            StudentPlanningCommand,
            StudentPlanningState,
        ):
            assert await database.scalar(select(func.count()).select_from(model)) == 0, (
                model.__name__
            )
    late_confirmation = await feed_env.client.post("/api/v1/student/lms-feeds", json=confirmation)
    assert late_confirmation.status_code == 404
    replay = await feed_env.client.post("/api/v1/student/data/reset", json=reset_command)
    assert replay.status_code == 200 and replay.json() == {"cleared": True, "reused": True}


@pytest.mark.asyncio
async def test_generic_disconnected_delete_data_erases_feed_and_derived_copies(feed_env):
    from navox.connectors.lifecycle import delete_learned_data, disconnect
    from navox.db.student_planning import StudentPlanningCommand, StudentTask

    feed, _, _ = await connect(feed_env)
    snapshot = (await feed_env.client.get("/api/v1/student/lms-feeds/items")).json()["items"][0]
    imported = await feed_env.client.post(
        "/api/v1/student/planning/tasks/from-assignment",
        json={
            "title": "Source-private lifecycle coursework",
            "request_id": str(uuid4()),
            "estimated_minutes": 30,
            "source_item_id": snapshot["id"],
            "source_revision": snapshot["revision"],
            "confirmed": True,
        },
    )
    assert imported.status_code == 201, imported.text
    manual = await feed_env.client.post(
        "/api/v1/student/planning/tasks",
        json={
            "title": "Keep independent task",
            "request_id": str(uuid4()),
            "estimated_minutes": 30,
        },
    )
    assert manual.status_code == 201, manual.text
    async with feed_env.factory() as database:
        connection_id = (await database.get(LmsFeed, UUID(feed["id"]))).connection_id
        await disconnect(
            database,
            workspace_id=feed_env.workspace_id,
            user_id=feed_env.user_id,
            connection_id=connection_id,
            request_id=uuid4(),
        )
    async with feed_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(ConnectionCredential)) == 0
        await delete_learned_data(
            database,
            workspace_id=feed_env.workspace_id,
            user_id=feed_env.user_id,
            connection_id=connection_id,
            request_id=uuid4(),
        )
    async with feed_env.factory() as database:
        for model in (LmsFeed, LmsFeedPreview, LmsFeedItem, LmsFeedSource, ConnectionCredential):
            assert await database.scalar(select(func.count()).select_from(model)) == 0
        tasks = list(await database.scalars(select(StudentTask)))
        assert [task.id for task in tasks] == [UUID(manual.json()["id"])]
        receipts = list(await database.scalars(select(StudentPlanningCommand)))
        assert receipts and all(
            "Source-private lifecycle coursework" not in str(row.result) for row in receipts
        )
        assert all(row.undo_snapshot is None for row in receipts)
        connection = await database.get(ConnectorConnection, connection_id)
        assert connection.status == "DISCONNECTED" and connection.config == {}
    assert (
        await sync_feed(
            feed_env.factory,
            feed_id=UUID(feed["id"]),
            user_id=feed_env.user_id,
            workspace_id=feed_env.workspace_id,
            settings=feed_env.settings,
            fetcher=feed_env.fetcher,
            now=datetime.now(UTC) + timedelta(hours=3),
        )
    ).state == "superseded"


@pytest.mark.asyncio
async def test_postgres_reset_retries_when_preview_commits_during_user_lock_wait(
    feed_env, monkeypatch
):
    if feed_env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Reset/create lock interleaving requires real PostgreSQL row locks")
    from navox.lms_feeds import service
    from navox.lms_feeds.schemas import PreviewCommand

    await connect(feed_env)
    waiting_for_user = asyncio.Event()
    original = service.require_owner

    async def observe_owner(database, **kwargs):
        if database is reset_database:
            waiting_for_user.set()
        return await original(database, **kwargs)

    monkeypatch.setattr(service, "require_owner", observe_owner)
    async with feed_env.factory() as creator, feed_env.factory() as reset_database:
        epoch = await service.reset_epoch(
            creator, user_id=feed_env.user_id, workspace_id=feed_env.workspace_id
        )
        await creator.scalar(select(User).where(User.id == feed_env.user_id).with_for_update())
        reset_task = asyncio.create_task(
            service.lock_all_feeds_for_reset(
                reset_database,
                user_id=feed_env.user_id,
                workspace_id=feed_env.workspace_id,
            )
        )
        try:
            await asyncio.wait_for(waiting_for_user.wait(), timeout=5)
            command = PreviewCommand(
                url=PRIVATE_URL, platform="canvas", timezone="UTC", all_day_due_time="23:59"
            )
            now = datetime.now(UTC)
            parsed = parse_feed(
                feed_env.fetcher.content,
                timezone="UTC",
                all_day_due_time="23:59",
                now=now,
                private_url=PRIVATE_URL,
            )
            await service.create_preview(
                creator,
                user_id=feed_env.user_id,
                workspace_id=feed_env.workspace_id,
                command=command,
                parsed=parsed,
                fetched=FeedResponse(200, feed_env.fetcher.content),
                settings=feed_env.settings,
                now=now,
                expected_reset=epoch,
            )
            with pytest.raises(FeedAccessError) as refusal:
                await asyncio.wait_for(reset_task, timeout=5)
            assert refusal.value.status == 409
            await reset_database.rollback()
        finally:
            if not reset_task.done():
                reset_task.cancel()
                await asyncio.gather(reset_task, return_exceptions=True)
            await creator.rollback()
    async with feed_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(LmsFeed)) == 2


@pytest.mark.asyncio
async def test_maximum_unicode_course_confirmation_fits_bounded_command(feed_env):
    from navox.db.student import StudentCourse

    labels = ["𠜎" * 118 + f"{index:02d}" for index in range(100)]
    feed_env.fetcher.content = calendar(
        *(event(str(index), f"[{label}] Homework") for index, label in enumerate(labels))
    )
    preview = (
        await feed_env.client.post(
            "/api/v1/student/lms-feeds/preview",
            json={
                "url": PRIVATE_URL,
                "platform": "canvas",
                "timezone": "UTC",
                "all_day_due_time": "23:59",
            },
        )
    ).json()
    course_id = uuid4()
    async with feed_env.factory() as database:
        database.add(
            StudentCourse(
                id=course_id,
                user_id=feed_env.user_id,
                workspace_id=feed_env.workspace_id,
                code="CS 2110",
                name="Course aliases",
                term_start=datetime.now(UTC).date(),
                term_end=(datetime.now(UTC) + timedelta(days=60)).date(),
            )
        )
        await database.commit()
    content = json.dumps(
        {
            "preview_token": preview["preview_token"],
            "preview_hash": preview["preview_hash"],
            "request_id": str(uuid4()),
            "name": "Courses",
            "course_matches": {label: str(course_id) for label in labels},
            "confirmed": True,
        },
        ensure_ascii=False,
    ).encode()
    assert 20_000 < len(content) < 64 * 1024
    result = await feed_env.client.post(
        "/api/v1/student/lms-feeds", content=content, headers={"Content-Type": "application/json"}
    )
    assert result.status_code == 201, result.text
