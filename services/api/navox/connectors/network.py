from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlsplit

from navox.connectors.contracts import ConnectorRuntimeError


def validate_public_https_origin(value: object, *, label: str = "base_url") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is required")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
    ):
        raise ValueError(f"{label} must be a clean HTTPS origin")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError(f"{label} cannot target a private or reserved address")
    port = f":{parsed.port}" if parsed.port else ""
    return f"https://{parsed.hostname}{port}"


def validate_https_endpoint(value: object, *, label: str = "endpoint_url") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is required")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be an HTTPS URL without credentials or query parameters")
    origin = validate_public_https_origin(
        f"https://{parsed.hostname}{f':{parsed.port}' if parsed.port else ''}",
        label=label,
    )
    path = parsed.path or "/"
    return f"{origin}{path}"


def join_relative_path(base_url: str, path: str) -> str:
    if not path.startswith("/") or "://" in path or "\\" in path:
        raise ValueError("Connector endpoint paths must be absolute relative paths")
    if any(segment == ".." for segment in path.split("/")):
        raise ValueError("Connector endpoint paths cannot traverse directories")
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def require_same_origin(url: str, base_url: str) -> None:
    candidate = urlsplit(url)
    base = urlsplit(base_url)
    if (
        candidate.scheme != "https"
        or candidate.hostname != base.hostname
        or candidate.port != base.port
    ):
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE",
            "Provider pagination attempted to leave the configured origin",
        )
