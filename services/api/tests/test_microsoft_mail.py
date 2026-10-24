"Microsoft adapter fixtures: bounded reads, fixed hosts, and exact sent-copy proof."

import base64
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from navox.core.settings import Settings
from navox.providers.microsoft_mail import (
    GRAPH,
    MailIdentity,
    MicrosoftMailError,
    MicrosoftMailGateway,
    action_reason,
    authorization_url,
    build_mime,
    normalize_scopes,
    parse_message,
    receipt_fingerprint,
    receipt_matches,
)


def source(**overrides):
    return {
        "id": "fixture/opaque+id",
        "subject": "Office hours",
        "from": {"emailAddress": {"address": "teacher@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "owner@example.com"}}],
        "replyTo": [],
        "body": {"contentType": "text", "content": "Could you meet on Tuesday?"},
        "bodyPreview": "Could you meet on Tuesday?",
        "sentDateTime": "2026-10-24T09:00:00Z",
        "internetMessageId": "<source@example.com>",
        "conversationId": "thread",
        "webLink": "https://outlook.office.com/mail/read/fixture",
        "flag": {"flagStatus": "notFlagged"},
        "isDraft": False,
        **overrides,
    }


def mime(**overrides):
    return build_mime(
        **{
            "sender": "owner@example.com",
            "to": "teacher@example.com",
            "subject": "Re: Office hours",
            "body": "Tuesday at 11 works for me.\nThanks.",
            "message_id": "<navox-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1@mail.navox.app>",
            "in_reply_to": "<source@example.com>",
            **overrides,
        }
    )


def test_triage_explains_the_real_candidate_signal_and_excludes_bulk_or_quotes():
    message = parse_message(source())
    assert (
        action_reason(message, "owner@example.com")
        == "Possible request: “Could you meet on Tuesday?”"
    )
    assert (
        action_reason(
            replace(message, body="For your information.", subject="Update"), "owner@example.com"
        )
        is None
    )
    assert (
        action_reason(replace(message, sender="newsletter@example.com"), "owner@example.com")
        is None
    )
    assert action_reason(replace(message, sender="owner@example.com"), "owner@example.com") is None
    assert action_reason(replace(message, to=("someone@example.com",)), "owner@example.com") is None
    assert (
        action_reason(
            replace(message, body="> Could you reply?", subject="Update"), "owner@example.com"
        )
        is None
    )
    assert (
        action_reason(replace(message, flagged=True), "owner@example.com")
        == "You flagged this email for follow-up."
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/mail",
        "https://outlook.office.com.evil.example",
        "http://outlook.office.com",
        "https://user:secret@outlook.office.com",
        "https://outlook.office.com:444/mail",
    ],
)
def test_untrusted_provider_open_urls_are_never_forwarded(url):
    assert parse_message(source(webLink=url)).open_url is None


def test_html_fallback_is_plain_text_and_reply_recipient_must_be_unambiguous():
    parsed = parse_message(
        source(
            body={
                "contentType": "html",
                "content": "<script>hidden()</script><p>Please confirm.</p>",
            }
        )
    )
    assert "hidden" not in parsed.body
    assert "Please confirm." in parsed.body
    with pytest.raises(MicrosoftMailError):
        parse_message(
            source(
                replyTo=[
                    {"emailAddress": {"address": "one@example.com"}},
                    {"emailAddress": {"address": "two@example.com"}},
                ]
            )
        )
    with pytest.raises(MicrosoftMailError):
        parse_message(
            source(
                **{
                    "from": {
                        "emailAddress": {"address": "teacher@example.com\r\nBcc: other@example.com"}
                    }
                }
            )
        )


def test_receipt_is_exact_and_includes_recipient_body_subject_thread_and_attachments():
    reviewed = mime()
    assert receipt_matches(reviewed, reviewed)
    assert receipt_matches(reviewed.replace(b"\r\n", b"\n"), reviewed)
    for changed in (
        mime(to="other@example.com"),
        mime(sender="other@example.com"),
        mime(subject="Changed"),
        mime(body="Changed text."),
        mime(in_reply_to="<other@example.com>"),
        reviewed.replace(
            b"To: teacher@example.com", b"To: teacher@example.com\r\nBcc: other@example.com"
        ),
        reviewed.replace(
            b"Subject: Re: Office hours", b"Subject: Re: Office hours\r\nSubject: Re: Office hours"
        ),
        reviewed.replace(b"Content-Type: text/plain", b"Content-Type: multipart/mixed"),
    ):
        assert not receipt_matches(changed, reviewed)
    assert len(receipt_fingerprint(reviewed)) == 64


@pytest.mark.asyncio
async def test_token_exchange_uses_pkce_fixed_tenant_and_provider_identity():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/token"):
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["code_verifier"] == ["v" * 64]
            assert form["redirect_uri"] == ["http://localhost:8000/api/v1/outlook/callback"]
            return httpx.Response(
                200,
                json={
                    "access_token": "opaque-access",
                    "refresh_token": "rotated-refresh",
                    "token_type": "Bearer",
                    "scope": "openid User.Read Mail.Read",
                },
            )
        assert request.headers["Authorization"] == "Bearer opaque-access"
        if request.url.path == "/oidc/userinfo":
            return httpx.Response(
                200,
                json={"sub": "protected-pairwise-subject", "email": "different-claim@example.com"},
            )
        assert request.url.path == "/v1.0/me"
        return httpx.Response(200, json={"id": "protected-graph-id", "mail": "owner@example.com"})

    settings = Settings(
        _env_file=None,
        microsoft_oauth_client_id="fixture",
        microsoft_oauth_client_secret="fixture-secret",
    )
    gateway = MicrosoftMailGateway(httpx.MockTransport(handler))
    token = await gateway.token(settings, code="fixture-code", verifier="v" * 64)
    assert token.refresh_token == "rotated-refresh"
    assert await gateway.identity(token.access_token) == MailIdentity(
        "protected-pairwise-subject", "protected-graph-id", "owner@example.com"
    )
    assert [call.url.host for call in calls] == [
        "login.microsoftonline.com",
        "graph.microsoft.com",
        "graph.microsoft.com",
    ]


def test_oauth_tenant_cannot_change_endpoint_and_scopes_cannot_gain_write_capability():
    settings = Settings(_env_file=None, microsoft_oauth_tenant="common/../../evil")
    with pytest.raises(MicrosoftMailError):
        authorization_url(
            settings, state="fixture", verifier="v" * 64, scopes=["Mail.Read"], login_hint=None
        )
    assert normalize_scopes(
        "https://graph.microsoft.com/Mail.Read Mail.ReadWrite Mail.Send.Shared"
    ) == {"Mail.Read"}


@pytest.mark.asyncio
async def test_bounded_inbox_page_never_follows_provider_next_link_and_encodes_id():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "graph.microsoft.com"
        assert 'outlook.body-content-type="text"' in request.headers["prefer"]
        if "mailFolders" in request.url.path:
            assert request.url.params["$top"] == "50"
            return httpx.Response(
                200,
                json={"value": [source()], "@odata.nextLink": "https://evil.example/collect-token"},
            )
        assert b"fixture%2Fopaque%2Bid" in request.url.raw_path
        return httpx.Response(200, json=source())

    gateway = MicrosoftMailGateway(httpx.MockTransport(handler))
    messages, more = await gateway.messages("fixture-access")
    assert more is True
    assert len(messages) == 1
    assert len(calls) == 1
    await gateway.message("fixture-access", "fixture/opaque+id")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_202_is_not_a_receipt_and_exact_verification_only_reads():
    reviewed = mime()
    message_id = "<navox-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1@mail.navox.app>"
    calls = []
    visible = False

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert str(request.url) == GRAPH + "/me/sendMail"
            assert request.headers["content-type"] == "text/plain"
            assert base64.b64decode(request.content) == reviewed
            return httpx.Response(202)
        if request.url.path.endswith("/$value"):
            return httpx.Response(200, content=reviewed)
        assert request.url.path.endswith("/mailFolders/sentitems/messages")
        assert request.url.params["$filter"] == f"internetMessageId eq '{message_id}'"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "receipt",
                        "internetMessageId": message_id,
                        "isDraft": False,
                        "sentDateTime": "2026-10-24T10:00:00Z",
                        "conversationId": "sent-thread",
                    }
                ]
                if visible
                else []
            },
        )

    gateway = MicrosoftMailGateway(httpx.MockTransport(handler))
    assert await gateway.send("fixture", reviewed) is None
    expected_hash = receipt_fingerprint(reviewed)
    assert await gateway.sent_receipt("fixture", message_id, expected_hash) is None
    visible = True
    receipt = await gateway.sent_receipt("fixture", message_id, expected_hash)
    assert receipt.id == "receipt"
    assert receipt.sent_at == datetime(2026, 10, 24, 10, tzinfo=UTC)
    assert receipt.conversation_id == "sent-thread"
    assert receipt.recipient == "teacher@example.com"
    assert sum(method == "POST" for method, _ in calls) == 1
    assert sum(method == "GET" for method, _ in calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["redirect", "overlarge", "malformed", "timeout"])
async def test_provider_failures_are_bounded_sanitized_and_never_retried(failure):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("SECRET provider diagnostics", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.example"}, text="SECRET")
        if failure == "overlarge":
            return httpx.Response(200, content=b"x" * 1_500_001)
        return httpx.Response(200, text="SECRET is not JSON")

    gateway = MicrosoftMailGateway(httpx.MockTransport(handler))
    with pytest.raises(MicrosoftMailError) as caught:
        await gateway.messages("fixture-secret-token")
    assert "SECRET" not in str(caught.value)
    assert "fixture-secret-token" not in str(caught.value)
    assert calls == 1


def reply_headers(**overrides):
    return {
        "id": "later-message",
        "conversationId": "conversation'opaque",
        "from": {"emailAddress": {"address": "teacher@example.com"}},
        "sender": {"emailAddress": {"address": "teacher@example.com"}},
        "toRecipients": [{"emailAddress": {"address": "owner@example.com"}}],
        "ccRecipients": [],
        "isDraft": False,
        "receivedDateTime": "2026-10-24T10:05:00Z",
        "internetMessageHeaders": [
            {
                "name": "In-Reply-To",
                "value": "<navox-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1@mail.navox.app>",
            },
            {"name": "Auto-Submitted", "value": "no"},
        ],
        **overrides,
    }


@pytest.mark.asyncio
async def test_conversation_check_reads_one_fixed_bounded_metadata_page_and_no_body():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET" and str(request.url).startswith(GRAPH + "/me/messages?")
        assert request.url.params["$top"] == "50"
        assert request.url.params["$filter"] == (
            "receivedDateTime gt 2026-10-24T10:00:00Z and conversationId eq 'conversation''opaque'"
        )
        assert request.url.params["$orderby"] == "receivedDateTime desc"
        fields = request.url.params["$select"].split(",")
        assert "body" not in fields and "bodyPreview" not in fields
        assert "internetMessageHeaders" in fields
        assert 'IdType="ImmutableId"' in request.headers["prefer"]
        return httpx.Response(
            200,
            json={
                "value": [reply_headers()],
                "@odata.nextLink": "https://untrusted.example/collect-token",
            },
        )

    gateway = MicrosoftMailGateway(httpx.MockTransport(handler))
    scan = await gateway.conversation_replies(
        "fixture", "conversation'opaque", datetime(2026, 10, 24, 10, tzinfo=UTC)
    )
    assert scan.has_more is True and scan.unreadable is False
    assert scan.checked_count == 1
    assert scan.messages[0].sender == "teacher@example.com"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"internetMessageHeaders": None},
        {"sender": None},
        {"receivedDateTime": "2026-10-24T10:05:00"},
        {"internetMessageHeaders": [{"name": "References", "value": "<one@example.com>"}] * 2},
    ],
)
async def test_missing_or_ambiguous_reply_metadata_marks_scan_incomplete(changes):
    gateway = MicrosoftMailGateway(
        httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"value": [reply_headers(**changes)]})
        )
    )
    scan = await gateway.conversation_replies(
        "fixture", "conversation'opaque", datetime(2026, 10, 24, 10, tzinfo=UTC)
    )
    assert scan.unreadable is True
    assert scan.messages == ()
    assert scan.checked_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exchange", [True, False])
async def test_absent_token_scope_inherits_only_explicit_verified_fallback(exchange):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "fixture-access",
                "refresh_token": "fixture-refresh",
                "token_type": "Bearer",
            },
        )

    settings = Settings(
        _env_file=None, microsoft_oauth_client_id="fixture", microsoft_oauth_client_secret="fixture"
    )
    gateway = MicrosoftMailGateway(httpx.MockTransport(handler))
    retained = frozenset({"openid", "User.Read", "Mail.Read"})
    value = await gateway.token(
        settings,
        code="fixture-code" if exchange else None,
        refresh="" if exchange else "fixture-refresh",
        verifier="v" * 64,
        fallback_scopes=retained,
    )
    assert value.scopes == retained
    assert "Mail.Send" not in value.scopes
    assert len(calls) == 1
    with pytest.raises(MicrosoftMailError):
        await gateway.token(settings, code="fixture-code", verifier="v" * 64)
    with pytest.raises(MicrosoftMailError):
        await gateway.token(
            settings, refresh="fixture-refresh", fallback_scopes=frozenset({"Mail.ReadWrite"})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, "", "  ", [], 12])
async def test_explicit_empty_or_invalid_token_scope_never_uses_fallback(scope):
    gateway = MicrosoftMailGateway(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "access_token": "fixture-access",
                    "refresh_token": "fixture-refresh",
                    "token_type": "Bearer",
                    "scope": scope,
                },
            )
        )
    )
    settings = Settings(
        _env_file=None, microsoft_oauth_client_id="fixture", microsoft_oauth_client_secret="fixture"
    )
    with pytest.raises(MicrosoftMailError):
        await gateway.token(
            settings,
            refresh="fixture-refresh",
            fallback_scopes=frozenset({"User.Read", "Mail.Read"}),
        )


@pytest.mark.asyncio
async def test_explicit_narrowed_scope_is_respected_instead_of_retaining_a_broader_grant():
    gateway = MicrosoftMailGateway(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "access_token": "fixture-access",
                    "refresh_token": "fixture-refresh",
                    "token_type": "Bearer",
                    "scope": "User.Read",
                },
            )
        )
    )
    settings = Settings(
        _env_file=None, microsoft_oauth_client_id="fixture", microsoft_oauth_client_secret="fixture"
    )
    value = await gateway.token(
        settings,
        refresh="fixture-refresh",
        fallback_scopes=frozenset({"User.Read", "Mail.Read", "Mail.Send"}),
    )
    assert value.scopes == {"User.Read"}


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True])
async def test_skipped_candidate_rows_always_mark_mailbox_coverage_incomplete(malformed):
    row = (
        source(
            replyTo=[
                {"emailAddress": {"address": "first@example.com"}},
                {"emailAddress": {"address": "second@example.com"}},
            ]
        )
        if not malformed
        else {"id": "unreadable"}
    )
    gateway = MicrosoftMailGateway(
        httpx.MockTransport(lambda _request: httpx.Response(200, json={"value": [row]}))
    )
    messages, incomplete = await gateway.messages("fixture")
    assert messages == []
    assert incomplete is True
    with pytest.raises(MicrosoftMailError):
        parse_message(row)
