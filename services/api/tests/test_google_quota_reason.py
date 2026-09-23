"""Quota diagnostics preserve exact allowlisted tokens, never infer quota scope."""

import json
from typing import Any

import httpx
import pytest

from navox.intelligence.source_cooldown import GoogleSourceCooldownError
from navox.intelligence.sync_errors import processing_diagnostic, sanitize_diagnostic
from navox.providers.google_sources import GoogleSourceError, GoogleSourceGateway

PRIVATE = "private-mail-token-project-and-provider-prose"


async def no_wait(_: float) -> None:
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,reasons,structured,expected",
    [
        (403, ["userRateLimitExceeded"], False, "userRateLimitExceeded"),
        (403, ["rateLimitExceeded"], False, "rateLimitExceeded"),
        (403, ["userRateLimitExceeded", "rateLimitExceeded"], False, None),
        (403, ["userRateLimitExceeded", PRIVATE], False, None),
        (403, ["rateLimitExceeded", "dailyLimitExceeded"], False, None),
        (403, ["RATE_LIMIT_EXCEEDED"], True, None),
        (403, ["userRateLimitExceeded"], True, None),
        (403, [PRIVATE], False, None),
        (429, ["userRateLimitExceeded"], False, None),
        (429, ["rateLimitExceeded"], False, None),
    ],
)
async def test_only_unambiguous_legacy_403_reason_is_preserved(
    status: int, reasons: list[str], structured: bool, expected: str | None
) -> None:
    details = [
        {"reason": reason, "message": PRIVATE, "metadata": {"project": PRIVATE}}
        for reason in reasons
    ]
    if structured:
        for detail in details:
            detail["@type"] = "type.googleapis.com/google.rpc.ErrorInfo"
    body = {"error": {"details" if structured else "errors": details, "message": PRIVATE}}
    gateway = GoogleSourceGateway(sleep=no_wait)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body))
    ) as client:
        with pytest.raises(GoogleSourceError) as caught:
            await gateway._get(client, "https://gmail.googleapis.com/" + PRIVATE)
    error = caught.value
    diagnostic = error.diagnostic()
    assert diagnostic.get("provider_reason") == expected
    assert "quota_scope" not in diagnostic
    assert PRIVATE not in repr(vars(error))
    assert PRIVATE not in json.dumps(diagnostic)
    # Persisted and Temporal diagnostics retain only the same safe category.
    assert processing_diagnostic(error) == diagnostic
    assert GoogleSourceCooldownError(diagnostic).diagnostic() == diagnostic


@pytest.mark.parametrize("reason", ["userRateLimitExceeded", "rateLimitExceeded"])
def test_allowlisted_reason_survives_sanitization_without_prose(reason: str) -> None:
    value = {
        "code": "google_rate_limited",
        "http_status": 403,
        "retry_after_seconds": 60,
        "provider_reason": reason,
    }
    assert sanitize_diagnostic({**value, "message": PRIVATE, "metadata": PRIVATE}) == value
    assert GoogleSourceCooldownError(value).diagnostic() == value


@pytest.mark.parametrize("reason", [PRIVATE, "RATE_LIMIT_EXCEEDED", [PRIVATE], None, True])
def test_unknown_or_malformed_reason_cannot_enter_diagnostics(reason: Any) -> None:
    expected = {"code": "google_rate_limited", "http_status": 403}
    assert sanitize_diagnostic({**expected, "provider_reason": reason}) == expected
    assert (
        GoogleSourceError(
            code="google_rate_limited", http_status=403, provider_reason=reason
        ).diagnostic()
        == expected
    )


@pytest.mark.parametrize(
    "code,status",
    [("google_rate_limited", 429), ("google_quota_exceeded", 403), ("google_source_error", 403)],
)
def test_reason_is_omitted_when_status_or_category_does_not_match(code: str, status: int) -> None:
    expected = {"code": code, "http_status": status}
    assert sanitize_diagnostic({**expected, "provider_reason": "userRateLimitExceeded"}) == expected
    assert (
        GoogleSourceError(
            code=code, http_status=status, provider_reason="userRateLimitExceeded"
        ).diagnostic()
        == expected
    )
