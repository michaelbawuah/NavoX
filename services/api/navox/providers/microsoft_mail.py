"""Bounded delegated Microsoft Graph mail operations.

Every network target is fixed by shipped code. No operation retries a send.
Graph's 202 response is acceptance, never proof of a sent message.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
from pydantic import EmailStr, TypeAdapter, ValidationError

from navox.core.settings import Settings

GRAPH = "https://graph.microsoft.com/v1.0"
USERINFO = "https://graph.microsoft.com/oidc/userinfo"
READ_SCOPES = ("openid", "profile", "email", "offline_access", "User.Read", "Mail.Read")
ALLOWED_SCOPES = (*READ_SCOPES, "Mail.Send", "Calendars.Read", "Calendars.ReadWrite")
READ_CAPABILITY = "communication.messages.read"
SEND_CAPABILITY = "communication.messages.send"
_EMAIL = TypeAdapter(EmailStr)
_SELECT = (
    "id,subject,from,toRecipients,replyTo,body,bodyPreview,receivedDateTime,"
    "sentDateTime,internetMessageId,conversationId,webLink,flag,isDraft"
)
REPLY_SCAN_LIMIT = 50
_REPLY_SELECT = (
    "id,conversationId,from,sender,toRecipients,ccRecipients,receivedDateTime,"
    "isDraft,internetMessageHeaders"
)


class MicrosoftMailError(ValueError):
    """Only fixed diagnostics escape a provider boundary."""

    def __init__(self, code: str = "provider_unavailable") -> None:
        self.code = code
        super().__init__("Outlook could not complete this request")


@dataclass(frozen=True)
class MailToken:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    scopes: frozenset[str]


@dataclass(frozen=True)
class MailIdentity:
    subject: str
    account_id: str
    email: str


@dataclass(frozen=True)
class MailReceipt:
    id: str
    sent_at: datetime
    conversation_id: str
    recipient: str


@dataclass(frozen=True)
class MailReply:
    id: str
    conversation_id: str
    sender: str
    from_address: str
    recipients: tuple[str, ...]
    received_at: datetime
    is_draft: bool
    references: tuple[str, ...]
    automated: bool


@dataclass(frozen=True)
class MailReplyScan:
    messages: tuple[MailReply, ...]
    checked_count: int
    has_more: bool
    unreadable: bool


@dataclass(frozen=True)
class MailMessage:
    id: str
    sender: str
    recipient: str
    to: tuple[str, ...]
    subject: str
    body: str
    preview: str
    sent_at: datetime
    internet_message_id: str
    conversation_id: str
    open_url: str | None
    flagged: bool
    is_draft: bool

    @property
    def source_hash(self) -> str:
        data = {
            "id": self.id,
            "sender": self.sender,
            "recipient": self.recipient,
            "to": self.to,
            "subject": self.subject,
            "body": self.body,
            "internet_message_id": self.internet_message_id,
        }
        return sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def email_address(value: object) -> str:
    try:
        return str(_EMAIL.validate_python(value))
    except ValidationError:
        raise MicrosoftMailError("invalid_response") from None


def normalize_scopes(value: object) -> frozenset[str]:
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise MicrosoftMailError("invalid_response")
    accepted = {item.casefold(): item for item in ALLOWED_SCOPES}
    scopes = set()
    for item in value.split():
        name = item.removeprefix("https://graph.microsoft.com/")
        if name.casefold() in accepted:
            scopes.add(accepted[name.casefold()])
    return frozenset(scopes)


def oauth_endpoint(settings: Settings, action: str) -> str:
    tenant = settings.microsoft_oauth_tenant
    if tenant not in {"common", "organizations", "consumers"} and not re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", tenant
    ):
        raise MicrosoftMailError("not_configured")
    if action not in {"authorize", "token"}:
        raise MicrosoftMailError("not_configured")
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{action}"


def authorization_url(
    settings: Settings, *, state: str, verifier: str, scopes: list[str], login_hint: str | None
) -> str:
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode("ascii")).digest()).rstrip(b"=")
    query = {
        "client_id": settings.microsoft_oauth_client_id,
        "response_type": "code",
        "redirect_uri": settings.microsoft_oauth_redirect_uri,
        "response_mode": "query",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge.decode(),
        "code_challenge_method": "S256",
        "prompt": "consent",
    }
    if login_hint:
        query["login_hint"] = login_hint
    return oauth_endpoint(settings, "authorize") + "?" + urlencode(query)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _address(row: object) -> str:
    if not isinstance(row, dict) or not isinstance(row.get("emailAddress"), dict):
        raise MicrosoftMailError("invalid_response")
    return email_address(row["emailAddress"].get("address"))


def _short(value: object, maximum: int, *, optional: bool = False) -> str:
    if optional and value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise MicrosoftMailError("invalid_response")
    return value


def _timestamp(value: object) -> datetime:
    try:
        timestamp = datetime.fromisoformat(_short(value, 64).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp.astimezone(UTC)
    except ValueError:
        raise MicrosoftMailError("invalid_response") from None


def parse_reply(raw: object) -> MailReply:
    """Header-only reply evidence; ambiguous participant formats are not proof."""
    if not isinstance(raw, dict) or not isinstance(raw.get("isDraft"), bool):
        raise MicrosoftMailError("invalid_response")
    to, cc = raw.get("toRecipients"), raw.get("ccRecipients")
    if not isinstance(to, list) or not isinstance(cc, list) or len(to) + len(cc) > 500:
        raise MicrosoftMailError("invalid_response")
    headers = raw.get("internetMessageHeaders")
    if not isinstance(headers, list) or len(headers) > 200:
        raise MicrosoftMailError("invalid_response")
    references: list[str] = []
    automated = False
    seen: set[str] = set()
    for header in headers:
        if not isinstance(header, dict):
            raise MicrosoftMailError("invalid_response")
        name = _short(header.get("name"), 128).casefold()
        value = _short(header.get("value"), 16_000)
        if name in {"in-reply-to", "references", "auto-submitted"}:
            if name in seen:
                raise MicrosoftMailError("invalid_response")
            seen.add(name)
        if name in {"in-reply-to", "references"}:
            references.extend(re.findall(r"<[^<>\s]{1,998}>", value))
        if name == "auto-submitted" and value.strip().casefold() != "no":
            automated = True
        if name == "content-type" and "multipart/report" in value.casefold():
            automated = True
    identifier = _short(raw.get("id"), 512)
    conversation = _short(raw.get("conversationId"), 512)
    if not identifier or not conversation:
        raise MicrosoftMailError("invalid_response")
    return MailReply(
        id=identifier,
        conversation_id=conversation,
        sender=_address(raw.get("sender")),
        from_address=_address(raw.get("from")),
        recipients=tuple(_address(row) for row in [*to, *cc]),
        received_at=_timestamp(raw.get("receivedDateTime")),
        is_draft=raw["isDraft"],
        references=tuple(references),
        automated=automated,
    )


def parse_message(raw: object) -> MailMessage:
    if not isinstance(raw, dict):
        raise MicrosoftMailError("invalid_response")
    sender = _address(raw.get("from"))
    reply = raw.get("replyTo") or []
    recipients = raw.get("toRecipients") or []
    if not isinstance(reply, list) or len(reply) > 1 or not isinstance(recipients, list):
        raise MicrosoftMailError("invalid_response")
    if len(recipients) > 256:
        raise MicrosoftMailError("invalid_response")
    body = raw.get("body")
    if not isinstance(body, dict):
        raise MicrosoftMailError("invalid_response")
    text = _short(body.get("content"), 200_000)
    if str(body.get("contentType", "")).casefold() == "html":
        parser = _Text()
        parser.feed(text)
        text = "".join(parser.parts)
    elif str(body.get("contentType", "")).casefold() != "text":
        raise MicrosoftMailError("invalid_response")
    try:
        timestamp = datetime.fromisoformat(
            _short(raw.get("sentDateTime") or raw.get("receivedDateTime"), 64).replace(
                "Z", "+00:00"
            )
        )
        if timestamp.tzinfo is None:
            raise ValueError
    except ValueError:
        raise MicrosoftMailError("invalid_response") from None
    link = raw.get("webLink")
    if isinstance(link, str) and len(link) <= 2000:
        try:
            parsed = urlsplit(link)
            valid_link = (
                parsed.scheme == "https"
                and parsed.hostname
                in {"outlook.office.com", "outlook.office365.com", "outlook.live.com"}
                and not parsed.username
                and not parsed.password
                and parsed.port in {None, 443}
            )
        except ValueError:
            valid_link = False
        if not valid_link:
            link = None
    else:
        link = None
    flag = raw.get("flag")
    return MailMessage(
        id=_short(raw.get("id"), 512),
        sender=sender,
        recipient=_address(reply[0]) if reply else sender,
        to=tuple(_address(row) for row in recipients),
        subject=_short(raw.get("subject"), 2000, optional=True),
        body=text,
        preview=_short(raw.get("bodyPreview"), 2000, optional=True),
        sent_at=timestamp.astimezone(UTC),
        internet_message_id=_short(raw.get("internetMessageId"), 998, optional=True),
        conversation_id=_short(raw.get("conversationId"), 512, optional=True),
        open_url=link,
        flagged=isinstance(flag, dict) and flag.get("flagStatus") == "flagged",
        is_draft=raw.get("isDraft") is not False,
    )


def action_reason(message: MailMessage, mailbox: str) -> str | None:
    "Conservative, disclosed candidates; never an invented urgency explanation."
    if message.is_draft or message.sender.casefold() == mailbox.casefold():
        return None
    if message.flagged:
        return "You flagged this email for follow-up."
    if mailbox.casefold() not in {item.casefold() for item in message.to}:
        return None
    if re.search(r"(?:no[._-]?reply|notification|newsletter)", message.sender, re.I):
        return None
    # Look only before quoted history; expose the actual line which triggered review.
    for line in (message.subject + "\n" + message.body[:6000]).splitlines()[:40]:
        clean = line.strip()
        if clean.startswith(">") or re.match(
            r"(?:On .+wrote:|From:|_{5,}|-{5,}Original)", clean, re.I
        ):
            break
        if 5 <= len(clean) <= 500 and (
            "?" in clean
            or re.search(r"\b(?:please|can you|could you|would you|let me know)\b", clean, re.I)
        ):
            return "Possible request: “" + clean[:200] + "”"
    return None


def build_mime(
    *, sender: str, to: str, subject: str, body: str, message_id: str, in_reply_to: str
) -> bytes:
    if any("\r" in value or "\n" in value for value in (sender, to, subject, message_id)):
        raise MicrosoftMailError("invalid_message")
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = email_address(sender)
    message["To"] = email_address(to)
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if in_reply_to and re.fullmatch(r"<[^<>\s]{1,950}>", in_reply_to):
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    return message.as_bytes()


def receipt_fingerprint(raw: bytes) -> str | None:
    "A content-free digest preserves exact verification after private text is cleared."
    try:
        received = BytesParser(policy=policy.default).parsebytes(raw)
        if (
            received.defects
            or received.is_multipart()
            or received.get_content_type() != "text/plain"
        ):
            return None
        content: dict[str, object] = {}
        for header in ("from", "to", "cc", "bcc"):
            content[header] = [
                email_address(address).casefold()
                for _, address in getaddresses(received.get_all(header, []))
            ]
        for header in ("subject", "message-id", "in-reply-to", "references"):
            if len(received.get_all(header, [])) > 1:
                return None
            content[header] = str(received.get(header, ""))
        body = received.get_content()
        if not isinstance(body, str):
            return None
        content["body"] = body.replace("\r\n", "\n")
        return sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (ValueError, UnicodeError, LookupError):
        return None


def receipt_matches(actual: bytes, expected: bytes) -> bool:
    "A provider receipt must match the approved MIME, including hidden recipients."
    actual_hash, expected_hash = receipt_fingerprint(actual), receipt_fingerprint(expected)
    return actual_hash is not None and actual_hash == expected_hash


class MicrosoftMailGateway:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        check: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.transport = transport
        self.check = check

    async def _request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        form: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        mime: bytes | None = None,
        maximum: int = 1_500_000,
    ) -> bytes:
        if self.check:
            await self.check()
        headers = {"Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"'}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if mime is not None:
            headers["Content-Type"] = "text/plain"
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(20, connect=5),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    method,
                    url,
                    headers=headers,
                    data=form,
                    params=params,
                    content=base64.b64encode(mime) if mime is not None else None,
                ) as response:
                    if response.status_code != (202 if mime is not None else 200):
                        code = {
                            401: "reauthorize",
                            403: "permission",
                            404: "not_found",
                            429: "rate_limit",
                        }.get(response.status_code, "provider_unavailable")
                        raise MicrosoftMailError(code)
                    data = bytearray()
                    async for part in response.aiter_bytes():
                        data.extend(part)
                        if len(data) > maximum:
                            raise MicrosoftMailError("invalid_response")
                    if self.check:
                        await self.check()
                    return bytes(data)
        except httpx.HTTPError:
            raise MicrosoftMailError() from None

    async def _json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        raw = await self._request(method, url, **kwargs)
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise MicrosoftMailError("invalid_response") from None
        if not isinstance(result, dict):
            raise MicrosoftMailError("invalid_response")
        return result

    async def token(
        self,
        settings: Settings,
        *,
        code: str | None = None,
        verifier: str = "",
        refresh: str = "",
        fallback_scopes: frozenset[str] | None = None,
    ) -> MailToken:
        secret = settings.microsoft_oauth_client_secret
        if (
            not settings.microsoft_oauth_client_id
            or secret is None
            or not secret.get_secret_value()
        ):
            raise MicrosoftMailError("not_configured")
        form = {
            "client_id": settings.microsoft_oauth_client_id,
            "client_secret": secret.get_secret_value(),
        }
        if code is not None:
            form.update(
                grant_type="authorization_code",
                code=code,
                code_verifier=verifier,
                redirect_uri=settings.microsoft_oauth_redirect_uri,
            )
        else:
            form.update(grant_type="refresh_token", refresh_token=refresh)
        raw = await self._json("POST", oauth_endpoint(settings, "token"), form=form, maximum=64_000)
        access = _short(raw.get("access_token"), 32_000)
        rotated = _short(raw.get("refresh_token") or refresh, 16_000)
        if not access or not rotated or str(raw.get("token_type", "")).casefold() != "bearer":
            raise MicrosoftMailError("invalid_response")
        if "scope" in raw:
            scopes = normalize_scopes(raw["scope"])
        else:
            # Only the verified consent request or previously stored grant supplies
            # absence-only fallback (OAuth 2.0 sections 5.1 and 6). Never widen it.
            if not fallback_scopes or not fallback_scopes.issubset(ALLOWED_SCOPES):
                raise MicrosoftMailError("invalid_response")
            scopes = fallback_scopes
        return MailToken(access, rotated, scopes)

    async def identity(self, token: str) -> MailIdentity:
        # Provider-protected identity, never unverified claims decoded from a token.
        claims = await self._json("GET", USERINFO, token=token, maximum=32_000)
        profile = await self._json(
            "GET",
            GRAPH + "/me",
            token=token,
            params={"$select": "id,mail,userPrincipalName"},
            maximum=32_000,
        )
        subject, account = _short(claims.get("sub"), 256), _short(profile.get("id"), 256)
        if not subject or not account:
            raise MicrosoftMailError("invalid_response")
        return MailIdentity(
            subject, account, email_address(profile.get("mail") or profile.get("userPrincipalName"))
        )

    async def messages(self, token: str) -> tuple[list[MailMessage], bool]:
        # Exactly one bounded page; no untrusted @odata.nextLink is followed.
        raw = await self._json(
            "GET",
            GRAPH + "/me/mailFolders/inbox/messages",
            token=token,
            params={"$top": "50", "$orderby": "receivedDateTime desc", "$select": _SELECT},
        )
        rows = raw.get("value")
        if not isinstance(rows, list) or len(rows) > 50:
            raise MicrosoftMailError("invalid_response")
        messages = []
        incomplete = bool(raw.get("@odata.nextLink"))
        for row in rows:
            try:
                messages.append(parse_message(row))
            except MicrosoftMailError:
                # Unsupported/malformed sender formats cannot authorize a reply.
                incomplete = True
                continue
        return messages, incomplete

    async def message(self, token: str, identifier: str) -> MailMessage:
        if not identifier or len(identifier) > 512 or any(ord(c) < 32 for c in identifier):
            raise MicrosoftMailError("invalid_message")
        raw = await self._json(
            "GET",
            GRAPH + "/me/messages/" + quote(identifier, safe=""),
            token=token,
            params={"$select": _SELECT},
        )
        message = parse_message(raw)
        if message.id != identifier or message.is_draft:
            raise MicrosoftMailError("invalid_response")
        return message

    async def send(self, token: str, mime: bytes) -> None:
        await self._request("POST", GRAPH + "/me/sendMail", token=token, mime=mime, maximum=32_000)

    async def sent_receipt(
        self, token: str, message_id: str, expected_hash: str
    ) -> MailReceipt | None:
        if not re.fullmatch(r"<navox-[0-9a-f]{32}-[1-9][0-9]*@mail\.navox\.app>", message_id):
            raise MicrosoftMailError("invalid_message")
        raw = await self._json(
            "GET",
            GRAPH + "/me/mailFolders/sentitems/messages",
            token=token,
            params={
                "$filter": f"internetMessageId eq '{message_id}'",
                "$select": "id,isDraft,internetMessageId,sentDateTime,conversationId",
                "$top": "2",
            },
        )
        rows = raw.get("value")
        if not isinstance(rows, list) or len(rows) != 1 or raw.get("@odata.nextLink"):
            return None
        row = rows[0]
        if (
            not isinstance(row, dict)
            or row.get("isDraft") is not False
            or row.get("internetMessageId") != message_id
            or not row.get("sentDateTime")
        ):
            return None
        identifier = _short(row.get("id"), 512)
        receipt = await self._request(
            "GET",
            GRAPH + "/me/messages/" + quote(identifier, safe="") + "/$value",
            token=token,
        )
        if receipt_fingerprint(receipt) != expected_hash:
            return None
        parsed = BytesParser(policy=policy.default).parsebytes(receipt)
        recipients = getaddresses(parsed.get_all("to", []))
        if len(recipients) != 1:
            return None
        return MailReceipt(
            id=identifier,
            sent_at=_timestamp(row.get("sentDateTime")),
            conversation_id=_short(row.get("conversationId"), 512, optional=True),
            recipient=email_address(recipients[0][1]),
        )

    async def conversation_replies(
        self, token: str, conversation_id: str, sent_at: datetime
    ) -> MailReplyScan:
        if (
            not conversation_id
            or len(conversation_id) > 512
            or any(ord(c) < 32 for c in conversation_id)
        ):
            raise MicrosoftMailError("invalid_message")
        if sent_at.tzinfo is None:
            raise MicrosoftMailError("invalid_message")
        # The ordered property precedes other filter properties, as Graph requires.
        # One metadata-only page is checked; no provider nextLink is followed.
        after = sent_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        quoted = conversation_id.replace("'", "''")
        raw = await self._json(
            "GET",
            GRAPH + "/me/messages",
            token=token,
            params={
                "$filter": f"receivedDateTime gt {after} and conversationId eq '{quoted}'",
                "$orderby": "receivedDateTime desc",
                "$top": str(REPLY_SCAN_LIMIT),
                "$select": _REPLY_SELECT,
            },
        )
        rows = raw.get("value")
        if not isinstance(rows, list) or len(rows) > REPLY_SCAN_LIMIT:
            raise MicrosoftMailError("invalid_response")
        messages: list[MailReply] = []
        unreadable = False
        for row in rows:
            try:
                messages.append(parse_reply(row))
            except MicrosoftMailError:
                unreadable = True
        return MailReplyScan(
            tuple(messages), len(rows), bool(raw.get("@odata.nextLink")), unreadable
        )
