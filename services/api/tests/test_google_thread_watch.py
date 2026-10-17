import copy
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from navox.providers import google_thread_watch as gateway_module
from navox.providers.google_thread_watch import (
    MAX_RESPONSE_BYTES,
    GoogleThreadWatchGateway,
    ThreadWatchMetadataError,
    _parse_thread,
    classify_reply,
)

NOW = datetime(2026, 10, 17, 8, tzinfo=UTC)
SENT_AT = NOW - timedelta(days=2)
OWNER = "owner@example.com"
TARGET = "person@example.com"


def message(
    identifier: str = "sent_1",
    *,
    sender: str = OWNER,
    recipient: str = TARGET,
    at: datetime = SENT_AT,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "threadId": "thread_1",
        "labelIds": labels if labels is not None else ["SENT"],
        "internalDate": str(int(at.timestamp() * 1000)),
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": recipient},
            ]
        },
    }


def thread(*messages: dict[str, Any]) -> dict[str, Any]:
    return {"id": "thread_1", "messages": list(messages) or [message()]}


def parse(data: Any) -> gateway_module.ThreadMetadata:
    return _parse_thread(json.dumps(data).encode(), thread_id="thread_1", now=NOW)


def classify(data: Any) -> gateway_module.ReplyEvidence | None:
    return classify_reply(parse(data), sent_message_id="sent_1", sender=OWNER, recipient=TARGET)


def test_reference_only_is_complete_negative_and_does_not_retain_message_content() -> None:
    data = thread(message())
    data["snippet"] = "private snippet"
    data["messages"][0]["payload"]["body"] = {"data": "private body"}
    metadata = parse(data)
    assert (
        classify_reply(metadata, sent_message_id="sent_1", sender=OWNER, recipient=TARGET) is None
    )
    assert "private" not in repr(metadata)
    assert metadata.messages[0].sender == OWNER
    assert metadata.messages[0].internal_date == SENT_AT


def test_only_later_incoming_matching_sender_and_owner_is_a_reply() -> None:
    data = thread(
        message(),
        message("wrong_author", sender="other@example.com", recipient=OWNER, labels=["INBOX"]),
        message("wrong_to", sender=TARGET, recipient="other@example.com", labels=["INBOX"]),
        message("older", sender=TARGET, recipient=OWNER, at=SENT_AT, labels=["INBOX"]),
        message(
            "later",
            sender="Person <PERSON@example.com>",
            recipient=OWNER,
            at=SENT_AT + timedelta(hours=2),
            labels=["INBOX", "UNREAD"],
        ),
        message(
            "first",
            sender=TARGET,
            recipient=f"Other <other@example.com>, {OWNER}",
            at=SENT_AT + timedelta(hours=1),
            labels=[],
        ),
    )
    evidence = classify(data)
    assert evidence is not None
    assert evidence.message_id == "first"
    assert evidence.received_at == SENT_AT + timedelta(hours=1)


@pytest.mark.parametrize("excluded_label", ["SENT", "DRAFT", "TRASH", "SPAM"])
def test_excluded_message_is_never_reply(excluded_label: str) -> None:
    assert (
        classify(
            thread(
                message(),
                message(
                    "ignored",
                    sender=TARGET,
                    recipient=OWNER,
                    at=SENT_AT + timedelta(hours=1),
                    labels=[excluded_label],
                ),
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda msg: msg.update(id="another_message"),
        lambda msg: msg.update(labelIds=["INBOX"]),
        lambda msg: msg.update(labelIds=["SENT", "DRAFT"]),
        lambda msg: msg["payload"]["headers"][0].update(value="other@example.com"),
        lambda msg: msg["payload"]["headers"][1].update(value="other@example.com"),
        lambda msg: msg["payload"]["headers"][1].update(value=f"{TARGET}, other@example.com"),
    ],
)
def test_missing_or_different_reference_cannot_prove_negative(
    change: Callable[[dict[str, Any]], Any],
) -> None:
    sent = message()
    change(sent)
    with pytest.raises(ThreadWatchMetadataError, match="thread_watch_reference_invalid"):
        classify(thread(sent))


@pytest.mark.parametrize(
    "change",
    [
        lambda msg: msg.update(id="bad/id"),
        lambda msg: msg.update(threadId="other"),
        lambda msg: msg.update(labelIds="SENT"),
        lambda msg: msg.update(labelIds=["SENT", "SENT"]),
        lambda msg: msg.update(labelIds=[{"id": "INBOX"}]),
        lambda msg: msg.update(internalDate=True),
        lambda msg: msg.update(internalDate="nan"),
        lambda msg: msg.update(internalDate="-1"),
        lambda msg: msg.update(internalDate="99999999999999999"),
        lambda msg: msg.update(
            internalDate=str(int((NOW + timedelta(minutes=6)).timestamp() * 1000))
        ),
        lambda msg: msg.update(payload=None),
        lambda msg: msg["payload"].update(headers=[]),
        lambda msg: msg["payload"]["headers"].append({"name": "from", "value": TARGET}),
        lambda msg: msg["payload"]["headers"].append({"name": "To", "value": OWNER}),
        lambda msg: msg["payload"]["headers"][0].update(value=f"{TARGET}, other@example.com"),
        lambda msg: msg["payload"]["headers"][0].update(value="bad address"),
        lambda msg: msg["payload"]["headers"][0].update(value="bad\r\nFrom: x@example.com"),
        lambda msg: msg["payload"]["headers"][1].update(value=None),
        lambda msg: msg["payload"]["headers"][1].update(value=f"{OWNER}, {OWNER}"),
    ],
)
def test_one_malformed_message_prevents_positive_or_negative_conclusion(
    change: Callable[[dict[str, Any]], Any],
) -> None:
    invalid = message(
        "reply", sender=TARGET, recipient=OWNER, at=SENT_AT + timedelta(hours=1), labels=["INBOX"]
    )
    change(invalid)
    with pytest.raises(ThreadWatchMetadataError, match="google_invalid_response"):
        classify(thread(message(), invalid))


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"id": "other", "messages": [message()]},
        {"id": "thread_1", "messages": []},
        thread(message(), message()),
        thread(*[message(str(index)) for index in range(501)]),
        {"id": "thread_1", "messages": [message(), None]},
    ],
)
def test_incomplete_or_unbounded_threads_are_invalid(data: Any) -> None:
    with pytest.raises(ThreadWatchMetadataError):
        parse(data)


def test_duplicate_json_keys_are_not_silently_overwritten() -> None:
    with pytest.raises(ThreadWatchMetadataError):
        _parse_thread(
            b'{"id":"other","id":"thread_1","messages":[]}',
            thread_id="thread_1",
            now=NOW,
        )


def install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[dict[str, Any]]:
    real_client = httpx.AsyncClient
    options: list[dict[str, Any]] = []

    def client(**kwargs: Any) -> httpx.AsyncClient:
        options.append(copy.copy(kwargs))
        return real_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", client)
    return options


@pytest.mark.asyncio
async def test_gateway_requests_metadata_only_with_fixed_connection_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=thread(message()))

    options = install_transport(monkeypatch, handler)
    metadata = await GoogleThreadWatchGateway().fetch(access_token="secret", thread_id="thread_1")
    assert metadata.id == "thread_1"
    assert options == [{"timeout": 10.0, "trust_env": False, "follow_redirects": False}]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url).startswith(f"{gateway_module.GMAIL_THREADS_ENDPOINT}/thread_1?")
    assert request.url.params["format"] == "metadata"
    assert request.url.params.get_list("metadataHeaders") == ["From", "To"]
    assert request.url.params["fields"] == (
        "id,messages(id,threadId,labelIds,internalDate,payload/headers)"
    )
    assert request.headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", ["../other", "a/b", "", "a?x=1", "a" * 513])
async def test_invalid_identifier_never_makes_a_network_request(
    monkeypatch: pytest.MonkeyPatch,
    thread_id: str,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Provider must not be called")

    options = install_transport(monkeypatch, handler)
    with pytest.raises(ThreadWatchMetadataError):
        await GoogleThreadWatchGateway().fetch(access_token="secret", thread_id=thread_id)
    assert not options


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {}, "google_authentication_failed"),
        (403, {}, "google_permission_denied"),
        (403, {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}, "google_rate_limited"),
        (
            403,
            {
                "error": {
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "RATE_LIMIT_EXCEEDED",
                        }
                    ]
                }
            },
            "google_rate_limited",
        ),
        (403, {"error": {"errors": [{"reason": "dailyLimitExceeded"}]}}, "google_rate_limited"),
        (
            403,
            {"error": {"errors": [{"reason": {"private": "value"}}]}},
            "google_permission_denied",
        ),
        (404, {}, "google_thread_not_found"),
        (429, {}, "google_rate_limited"),
        (503, {}, "google_provider_unavailable"),
        (302, {}, "google_invalid_response"),
    ],
)
async def test_http_failures_have_fixed_diagnostics_and_actual_status(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: dict[str, Any],
    code: str,
) -> None:
    install_transport(
        monkeypatch,
        lambda _: httpx.Response(
            status,
            json={**body, "private_error": "Do not retain me"},
            headers={"Retry-After": "999999", "Location": "https://private.example.com"},
        ),
    )
    with pytest.raises(ThreadWatchMetadataError) as raised:
        await GoogleThreadWatchGateway().fetch(access_token="secret", thread_id="thread_1")
    assert str(raised.value) == code
    assert raised.value.code == code
    assert raised.value.http_status == status
    assert raised.value.retry_after_seconds == 86_400
    assert raised.value.__cause__ is None
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("header", ["", "-1", "not a date", "0", "2" * 129])
def test_malformed_retry_after_is_not_retained(header: str) -> None:
    response = httpx.Response(429, headers={"Retry-After": header})
    assert gateway_module._retry_after(response, now=NOW) is None


def test_retry_after_http_date_is_a_bounded_delay() -> None:
    response = httpx.Response(429, headers={"Retry-After": "Sat, 17 Oct 2026 08:02:00 GMT"})
    assert gateway_module._retry_after(response, now=NOW) == 120


@pytest.mark.asyncio
@pytest.mark.parametrize("access_token", ["", "bad token", "bad\r\nAuthorization:x"])
async def test_bad_token_never_makes_a_network_request(
    monkeypatch: pytest.MonkeyPatch,
    access_token: str,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Provider must not be called")

    options = install_transport(monkeypatch, handler)
    with pytest.raises(ThreadWatchMetadataError, match="google_authentication_failed"):
        await GoogleThreadWatchGateway().fetch(access_token=access_token, thread_id="thread_1")
    assert not options


class HugeStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(34):
            yield b"x" * 65_536


@pytest.mark.asyncio
async def test_streaming_response_is_bounded_before_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert MAX_RESPONSE_BYTES == 2 * 1024 * 1024
    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=HugeStream()))
    with pytest.raises(ThreadWatchMetadataError, match="google_invalid_response"):
        await GoogleThreadWatchGateway().fetch(access_token="secret", thread_id="thread_1")


@pytest.mark.asyncio
async def test_transport_failure_has_no_url_token_or_provider_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private request secret", request=request)

    install_transport(monkeypatch, handler)
    with pytest.raises(ThreadWatchMetadataError) as raised:
        await GoogleThreadWatchGateway().fetch(access_token="secret", thread_id="thread_1")
    assert str(raised.value) == "google_transport_error"
    assert raised.value.http_status is None
    assert raised.value.__cause__ is None
