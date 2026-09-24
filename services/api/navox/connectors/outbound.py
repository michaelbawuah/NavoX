"""Approved-origin HTTPS transport with DNS pinning and bounded JSON responses.

The peer is a validated numeric address; Host/SNI still name the approved host.
No ambient proxy, redirect, cookie, arbitrary port, or credential-bearing URL.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from navox.connectors.contracts import ConnectorRuntimeError

Resolver = Callable[[str], Awaitable[list[str]]]
MAX_RESPONSE_BYTES = 2_000_000


def approved_origin(value: str) -> str:
    try:
        p = urlsplit(value)
        host = p.hostname or ""
        if (
            p.scheme != "https"
            or not host
            or p.username
            or p.password
            or p.port not in {None, 443}
            or p.path not in {"", "/"}
            or p.query
            or p.fragment
            or "\\" in value
            or "%" in host
            or host.endswith(".")
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError
        host = host.encode("idna").decode("ascii").lower()
        if "." not in host or host.endswith((".local", ".localhost", ".internal")):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        return f"https://{host}"
    except (ValueError, UnicodeError):
        raise ValueError("Configure a public HTTPS hostname on port 443") from None


def validate_target(url: httpx.URL, origin: str) -> None:
    raw = str(url)
    decoded = unquote(raw)
    if (
        url.scheme != "https"
        or url.host != httpx.URL(origin).host
        or url.port not in {None, 443}
        or url.username
        or url.password
        or url.fragment
        or "\\" in decoded
        or any(ord(c) <= 32 or ord(c) == 127 for c in decoded)
        or any(p in {".", ".."} for p in unquote(url.path).split("/"))
        or len(raw) > 4_096
    ):
        raise ConnectorRuntimeError("PERMISSION_DENIED", "Outbound origin or path is not approved")
    forbidden = {"access_token", "token", "api_key", "authorization", "client_secret", "code"}
    if any(k.lower() in forbidden for k, _ in parse_qsl(url.query.decode("ascii"))):
        raise ConnectorRuntimeError(
            "PERMISSION_DENIED", "Credentials cannot appear in request URLs"
        )


async def resolve_public(host: str) -> list[str]:
    try:
        async with asyncio.timeout(5):
            entries = await asyncio.get_running_loop().getaddrinfo(
                host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )
        return list(dict.fromkeys(str(e[4][0]) for e in entries))
    except (OSError, TimeoutError):
        raise ConnectorRuntimeError(
            "PROVIDER_UNAVAILABLE", "Provider name resolution failed"
        ) from None


def public_addresses(values: list[str]) -> list[str]:
    if not values or len(values) > 64:
        raise ConnectorRuntimeError("PERMISSION_DENIED", "Provider address is not approved")
    result = []
    for value in values:
        try:
            address = ipaddress.ip_address(value)
            if not address.is_global or address.is_multicast:
                raise ValueError
            if isinstance(address, ipaddress.IPv6Address) and (
                address.ipv4_mapped
                or address.sixtofour
                or address.teredo
                or address in ipaddress.ip_network("64:ff9b::/96")
                or address in ipaddress.ip_network("64:ff9b:1::/48")
            ):
                raise ValueError
        except ValueError:
            raise ConnectorRuntimeError(
                "PERMISSION_DENIED", "Provider address is not approved"
            ) from None
        result.append(str(address))
    return result


class ApprovedHTTPSTransport(httpx.AsyncBaseTransport):
    """Reusable by trusted adapters; origin selection comes from deployment policy."""

    def __init__(
        self,
        origin: str,
        *,
        resolver: Resolver = resolve_public,
        transport: httpx.AsyncBaseTransport | None = None,
        allowed_post_paths: frozenset[str] = frozenset(),
    ) -> None:
        self.origin = approved_origin(origin)
        self.resolver = resolver
        if any(
            not path.startswith("/")
            or "?" in path
            or "#" in path
            or "\\" in path
            or any(segment in {".", ".."} for segment in path.split("/"))
            for path in allowed_post_paths
        ):
            raise ValueError("POST paths must be fixed absolute paths")
        self.allowed_post_paths = allowed_post_paths
        self.transport = transport or httpx.AsyncHTTPTransport(
            verify=True, trust_env=False, retries=0
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_target(request.url, self.origin)
        if request.method != "GET" and not (
            request.method == "POST"
            and request.url.path in self.allowed_post_paths | {"/login/oauth2/token"}
        ):
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Outbound method is not approved")
        addresses = public_addresses(await self.resolver(request.url.host))
        headers = httpx.Headers(request.headers)
        headers["Host"] = request.url.host
        headers["Accept-Encoding"] = "identity"
        headers.pop("cookie", None)
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host},
        )
        response = None
        try:
            async with asyncio.timeout(30):
                response = await self.transport.handle_async_request(pinned)
                if 300 <= response.status_code < 400:
                    raise ConnectorRuntimeError(
                        "INVALID_PROVIDER_RESPONSE", "Provider redirects are not allowed"
                    )
                encoding = response.headers.get("content-encoding", "identity").lower()
                if encoding not in {"", "identity"}:
                    raise ConnectorRuntimeError(
                        "INVALID_PROVIDER_RESPONSE",
                        "Compressed provider responses are not accepted",
                    )
                if response.status_code >= 400:
                    return httpx.Response(
                        response.status_code,
                        headers={"retry-after": response.headers.get("retry-after", "")},
                        content=b"",
                    )
                content = bytearray()
                if response.is_stream_consumed:
                    if len(response.content) > MAX_RESPONSE_BYTES:
                        raise ConnectorRuntimeError(
                            "INVALID_PROVIDER_RESPONSE", "Provider response is too large"
                        )
                    content.extend(response.content)
                else:
                    async for part in response.aiter_raw():
                        if len(content) + len(part) > MAX_RESPONSE_BYTES:
                            raise ConnectorRuntimeError(
                                "INVALID_PROVIDER_RESPONSE", "Provider response is too large"
                            )
                        content.extend(part)
                clean_headers = [
                    (k, v)
                    for k, v in response.headers.multi_items()
                    if k.lower() not in {"set-cookie", "content-length"}
                ]
                return httpx.Response(
                    response.status_code, headers=clean_headers, content=bytes(content)
                )
        except (httpx.HTTPError, OSError, TimeoutError):
            raise ConnectorRuntimeError("PROVIDER_UNAVAILABLE", "Provider request failed") from None
        finally:
            if response is not None:
                await response.aclose()

    async def aclose(self) -> None:
        await self.transport.aclose()
