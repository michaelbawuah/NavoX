"""Provider pacing and retry tests use virtual time, never real sleeps."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from navox.providers.google_sources import (
    GoogleSourceAuthorizationError,
    GoogleSourceError,
    GoogleSourceGateway,
)

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
EPOCH = datetime(2026, 9, 23, tzinfo=UTC)
PRIVATE = "private-message-and-project-token"


@dataclass
class Clock:
    value: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.value

    def now(self) -> datetime:
        return EPOCH + timedelta(seconds=self.value)

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.value += delay

    def gateway(self, transport: httpx.AsyncBaseTransport | None = None) -> GoogleSourceGateway:
        return GoogleSourceGateway(
            transport=transport,
            sleep=self.sleep,
            monotonic=self.monotonic,
            now=self.now,
            jitter=lambda: 0.0,
        )


def failure(status: int, reason: str, retry_after: str | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"Retry-After": retry_after} if retry_after is not None else {},
        json={"error": {"message": PRIVATE, "errors": [{"reason": reason, "message": PRIVATE}]}},
    )


@pytest.mark.asyncio
async def test_late_message_retry_preserves_already_fetched_messages() -> None:
    clock = Clock()
    requests: list[tuple[str, float]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/gmail/v1/users/me")
        requests.append((path, clock.value))
        if path == "/profile":
            return httpx.Response(200, json={"historyId": "anchor"})
        if path == "/messages":
            return httpx.Response(200, json={"messages": [{"id": "first"}, {"id": "second"}]})
        if path == "/messages/second" and len(requests) == 4:
            return failure(429, "rateLimitExceeded")
        return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1], "payload": {}})

    batch = await clock.gateway(httpx.MockTransport(handle)).fetch(
        source="gmail",
        access_token=PRIVATE,
        workspace_id=uuid4(),
        connection_id=uuid4(),
        cursor=None,
        now=EPOCH,
    )
    assert [path for path, _ in requests] == [
        "/profile",
        "/messages",
        "/messages/first",
        "/messages/second",
        "/messages/second",
    ]
    assert all(b[1] - a[1] >= 0.25 for a, b in zip(requests, requests[1:], strict=False))
    assert [document.external_id for document in batch.documents] == ["first", "second"]
    assert batch.cursor == "anchor"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [(403, "userRateLimitExceeded"), (429, ""), (503, "")])
async def test_transient_failure_retries_same_get_with_exponential_delay(
    status: int, reason: str
) -> None:
    clock = Clock()
    requests: list[tuple[str, float]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), clock.value))
        return failure(status, reason) if len(requests) < 3 else httpx.Response(200, json={"ok": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await clock.gateway()._get(client, GMAIL + "/messages/id", {"format": "full"}) == {
            "ok": 1
        }
    assert len({url for url, _ in requests}) == 1
    assert [at for _, at in requests] == [0.0, 1.0, 3.0]
    assert clock.sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_jitter_is_added_to_exponential_backoff() -> None:
    clock = Clock()
    starts: list[float] = []

    def handle(_: httpx.Request) -> httpx.Response:
        starts.append(clock.value)
        return failure(503, "") if len(starts) < 3 else httpx.Response(200, json={})

    gateway = GoogleSourceGateway(
        sleep=clock.sleep, monotonic=clock.monotonic, now=clock.now, jitter=lambda: 0.5
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await gateway._get(client, CALENDAR)
    assert starts == [0.0, 1.5, 4.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [(403, "rateLimitExceeded"), (429, ""), (500, "")])
async def test_exhausted_transient_failure_has_bounded_safe_cooldown(
    status: int, reason: str
) -> None:
    clock = Clock()
    requests: list[float] = []

    def handle(_: httpx.Request) -> httpx.Response:
        requests.append(clock.value)
        return failure(status, reason)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GoogleSourceError) as caught:
            await clock.gateway()._get(client, GMAIL + "/messages/id")
    assert len(requests) == 3
    assert caught.value.diagnostic() == {
        "code": "google_provider_unavailable" if status >= 500 else "google_rate_limited",
        "http_status": status,
        "retry_after_seconds": 60,
    }
    assert isinstance(caught.value, GoogleSourceAuthorizationError) == (status == 403)
    assert PRIVATE not in repr(vars(caught.value))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,code",
    [
        ("dailyLimitExceeded", "google_daily_limit_exceeded"),
        ("quotaExceeded", "google_quota_exceeded"),
    ],
)
async def test_hard_quota_is_not_retried_inline(reason: str, code: str) -> None:
    clock = Clock()
    requests = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return failure(403, reason)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GoogleSourceAuthorizationError) as caught:
            await clock.gateway()._get(client, GMAIL + "/messages/id")
    assert requests == 1
    assert not clock.sleeps
    assert caught.value.diagnostic() == {
        "code": code,
        "http_status": 403,
        "retry_after_seconds": 300,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hint,expected",
    [
        ("10", 10.0),
        ("Wed, 23 Sep 2026 00:00:10 GMT", 10.0),
        ("-10", 1.0),
        ("NaN", 1.0),
        (PRIVATE, 1.0),
        ("0.5", 1.0),
        ("Tue, 22 Sep 2026 00:00:00 GMT", 1.0),
    ],
)
async def test_retry_after_accepts_seconds_or_future_http_date_only(
    hint: str, expected: float
) -> None:
    clock = Clock()
    starts: list[float] = []

    def handle(_: httpx.Request) -> httpx.Response:
        starts.append(clock.value)
        return failure(429, "", hint) if len(starts) == 1 else httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await clock.gateway()._get(client, GMAIL + "/messages/id")
    assert starts == [0.0, expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("hint,expected", [("61", 61), ("999999999", 86400)])
async def test_long_retry_after_goes_to_durable_cooldown_without_inline_wait(
    hint: str, expected: int
) -> None:
    clock = Clock()
    requests = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return failure(429, "", hint)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GoogleSourceError) as caught:
            await clock.gateway()._get(client, GMAIL + "/messages/id")
    assert requests == 1
    assert not clock.sleeps
    assert caught.value.retry_after_seconds == expected


@pytest.mark.asyncio
async def test_total_inline_backoff_never_exceeds_sixty_seconds() -> None:
    clock = Clock()
    starts: list[float] = []

    def handle(_: httpx.Request) -> httpx.Response:
        starts.append(clock.value)
        return failure(429, "", "60")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GoogleSourceError):
            await clock.gateway()._get(client, GMAIL + "/messages/id")
    assert starts == [0.0, 60.0]
    assert clock.sleeps == [60]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,reason", [(401, ""), (403, "accessNotConfigured"), (400, ""), (404, "")]
)
async def test_nontransient_failures_are_not_retried(status: int, reason: str) -> None:
    clock = Clock()
    requests = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return failure(status, reason)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GoogleSourceError):
            await clock.gateway()._get(client, GMAIL + "/messages/id")
    assert requests == 1
    assert not clock.sleeps


@pytest.mark.asyncio
async def test_gmail_pacing_is_per_gateway_and_calendar_is_not_paced() -> None:
    clock = Clock()
    starts: list[float] = []

    def handle(_: httpx.Request) -> httpx.Response:
        starts.append(clock.value)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = clock.gateway()
        await gateway._get(client, GMAIL + "/profile")
        await gateway._get(client, GMAIL + "/messages")
        await gateway._get(client, CALENDAR)
        await gateway._get(client, GMAIL + "/messages/id")
        await clock.gateway()._get(client, GMAIL + "/profile")
    assert starts == [0.0, 0.25, 0.25, 0.5, 0.5]


@pytest.mark.parametrize("value", [None, True, False, "60", 1.5, 0, -1, 86401])
def test_retry_after_diagnostic_rejects_unvalidated_values(value: Any) -> None:
    assert GoogleSourceError(retry_after_seconds=value).diagnostic() == {
        "code": "google_source_error"
    }


@pytest.mark.parametrize("value", [1, 60, 86400])
def test_retry_after_diagnostic_retains_only_bounded_integer(value: int) -> None:
    assert GoogleSourceError(retry_after_seconds=value).diagnostic() == {
        "code": "google_source_error",
        "retry_after_seconds": value,
    }
