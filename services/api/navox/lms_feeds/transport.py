"""Private feed URLs are used only in a pinned GET, never an HTTPX client log.

Unlike generic connectors, a calendar subscription can carry a token in its
query/path. That exception is local to this transport: no redirects, auth/cookie
headers, ambient proxy, arbitrary port, or second DNS lookup is permitted.
"""

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.outbound import Resolver, approved_origin, public_addresses, resolve_public

MAX_FEED_BYTES = 5 * 1024 * 1024
_fetch_slots = asyncio.Semaphore(4)


class FeedError(ValueError):
    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def feed_domain(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        approved_origin(f"https://{host}")
        if (
            parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not parsed.path.startswith("/")
            or "\\" in unquote(value)
            or any(ord(c) <= 32 or ord(c) == 127 for c in unquote(value))
            or len(value) > 4_096
        ):
            raise ValueError
        canonical = host.encode("idna").decode("ascii").lower()
        if len(canonical) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in canonical.split(".")
        ):
            raise ValueError
        value.encode("utf-8")
        httpx.URL(value)
        return canonical
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise FeedError("invalid_feed_url") from None


def validator_header(value: str | None, limit: int = 512) -> str | None:
    if value is None:
        return None
    if len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value


def private_validator(value: str | None, private_url: str, limit: int = 512) -> str | None:
    valid = validator_header(value, limit)
    if valid is None:
        return None
    decoded = unquote(valid)
    private = urlsplit(private_url)
    values = [value for _, value in parse_qsl(private.query) if len(value) >= 3]
    if "://" in decoded or any(secret in decoded for secret in values):
        return None
    return valid


@dataclass(frozen=True)
class FeedResponse:
    status: int
    content: bytes
    etag: str | None = None
    last_modified: str | None = None


class FeedFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = resolve_public,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.resolver = resolver
        self.transport = transport

    async def fetch(
        self,
        private_url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FeedResponse:
        try:
            async with asyncio.timeout(10):
                async with _fetch_slots:
                    return await self._fetch(private_url, etag=etag, last_modified=last_modified)
        except TimeoutError:
            raise FeedError("feed_unavailable") from None

    async def _fetch(
        self,
        private_url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FeedResponse:
        host = feed_domain(private_url)
        response: httpx.Response | None = None
        transport = self.transport or httpx.AsyncHTTPTransport(
            verify=True,
            trust_env=False,
            retries=0,
        )
        try:
            async with asyncio.timeout(10):
                addresses = public_addresses(await self.resolver(host))
                headers = {"Host": host, "Accept": "text/calendar", "Accept-Encoding": "identity"}
                if validator_header(etag):
                    headers["If-None-Match"] = str(etag)
                if validator_header(last_modified, 128):
                    headers["If-Modified-Since"] = str(last_modified)
                url = httpx.URL(private_url).copy_with(host=addresses[0])
                request = httpx.Request(
                    "GET",
                    url,
                    headers=headers,
                    extensions={
                        "sni_hostname": host,
                        "timeout": {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0},
                    },
                )
                response = await transport.handle_async_request(request)
                if response.status_code == 304:
                    return FeedResponse(
                        304,
                        b"",
                        private_validator(response.headers.get("etag"), private_url),
                        private_validator(response.headers.get("last-modified"), private_url, 128),
                    )
                if response.status_code != 200:
                    raise FeedError("feed_http_error", status=response.status_code)
                if response.headers.get("content-encoding", "identity").lower() not in {
                    "",
                    "identity",
                }:
                    raise FeedError("compressed_feed_not_supported")
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        size = int(length)
                    except ValueError:
                        raise FeedError("invalid_feed_response") from None
                    if size < 0 or size > MAX_FEED_BYTES:
                        raise FeedError("feed_too_large")
                body = bytearray()
                if response.is_stream_consumed:
                    body.extend(response.content)
                    if len(body) > MAX_FEED_BYTES:
                        raise FeedError("feed_too_large")
                else:
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > MAX_FEED_BYTES:
                            raise FeedError("feed_too_large")
                        body.extend(chunk)
                return FeedResponse(
                    200,
                    bytes(body),
                    private_validator(response.headers.get("etag"), private_url),
                    private_validator(response.headers.get("last-modified"), private_url, 128),
                )
        except FeedError:
            raise
        except (httpx.HTTPError, OSError, TimeoutError, ConnectorRuntimeError):
            raise FeedError("feed_unavailable") from None
        finally:
            if response is not None:
                await response.aclose()
            if self.transport is None:
                await transport.aclose()
