import base64
import json
from email import message_from_bytes

import httpx
import pytest

from navox.providers.google_gmail import (
    GmailProviderError,
    GmailReplyMetadata,
    GmailSendPayload,
    GoogleGmailGateway,
)
from navox.providers.google_sources import GoogleSourceError, GoogleSourceGateway


def source_metadata():
    return {
        "id": "source-123",
        "threadId": "thread-456",
        "payload": {
            "headers": [
                {"name": "Message-ID", "value": "<source@example.com>"},
                {"name": "Subject", "value": "Synthetic review"},
                {"name": "References", "value": "<parent@example.com>"},
            ]
        },
    }


def reply_metadata():
    return GmailReplyMetadata(
        source_message_id="source-123",
        thread_id="thread-456",
        source_subject="Synthetic review",
        in_reply_to="<source@example.com>",
        references="<parent@example.com> <source@example.com>",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_id", ["thread-456", "different-thread", None])
async def test_reply_wire_and_receipt_thread_validation(monkeypatch, thread_id):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "sent-789", "threadId": thread_id})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr("navox.providers.google_gmail.httpx.AsyncClient", lambda **_: client)
    payload = GmailSendPayload(
        sender="owner@example.com",
        to="owner@example.com",
        subject="Re: Synthetic review",
        body_text="Synthetic review complete.",
        reply=reply_metadata(),
    )
    if thread_id == "thread-456":
        receipt = await GoogleGmailGateway().send(
            access_token="fixture", payload=payload, idempotency_key="fixture"
        )
        assert receipt.thread_id == "thread-456"
    else:
        with pytest.raises(GmailProviderError):
            await GoogleGmailGateway().send(
                access_token="fixture", payload=payload, idempotency_key="fixture"
            )
    assert len(requests) == 1  # ambiguous receipts are never retried
    wire = json.loads(requests[0].content)
    assert set(wire) == {"raw", "threadId"}
    assert wire["threadId"] == "thread-456"
    message = message_from_bytes(base64.urlsafe_b64decode(wire["raw"]))
    assert message["In-Reply-To"] == "<source@example.com>"
    assert message["References"] == "<parent@example.com> <source@example.com>"
    assert message["Subject"] == payload.subject
    assert message["From"] == message["To"] == "owner@example.com"
    assert message["Cc"] is None and message["Bcc"] is None
    assert message.get_payload() == payload.body_text + "\n"
    assert not message.is_multipart()


@pytest.mark.asyncio
@pytest.mark.parametrize("ancestry", ["references", "in-reply-to", "none"])
async def test_resolve_only_selected_message_headers(ancestry):
    requests = []
    data = source_metadata()
    headers = data["payload"]["headers"]
    if ancestry == "none":
        headers.pop()
    elif ancestry == "in-reply-to":
        headers[-1]["name"] = "In-Reply-To"

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=data)

    reply = await GoogleSourceGateway(transport=httpx.MockTransport(respond)).gmail_reply_metadata(
        "fixture", external_id="source-123"
    )
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/gmail/v1/users/me/messages/source-123"
    assert dict(requests[0].url.params) == {
        "format": "metadata",
        "fields": "id,threadId,payload/headers",
    }
    assert reply.source_message_id == "source-123" and reply.thread_id == "thread-456"
    assert reply.references == (
        "<source@example.com>"
        if ancestry == "none"
        else "<parent@example.com> <source@example.com>"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption", ["wrong_message", "missing_thread", "duplicate_id", "injection", "bad_references"]
)
async def test_unusable_reply_metadata_fails_closed(corruption):
    data = source_metadata()
    headers = data["payload"]["headers"]
    if corruption == "wrong_message":
        data["id"] = "other-message"
    elif corruption == "missing_thread":
        data.pop("threadId")
    elif corruption == "duplicate_id":
        headers.append({"name": "Message-ID", "value": "<other@example.com>"})
    elif corruption == "injection":
        headers[0]["value"] += "\r\nBcc: attacker@example.com"
    else:
        headers[-1]["value"] = "not-a-message-id"
    gateway = GoogleSourceGateway(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    )
    with pytest.raises(GoogleSourceError):
        await gateway.gmail_reply_metadata("fixture", external_id="source-123")
