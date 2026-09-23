import json
import traceback
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from navox.providers.google_sources import (
    ExpiredSourceCursor,
    GoogleSourceAuthorizationError,
    GoogleSourceError,
    GoogleSourceGateway,
    gmail_document,
)

PRIVATE = "private-mailbox-token-project-and-message"


async def no_wait(_: float) -> None:
    pass


def provider_error(reason: str, *, structured: bool = False) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "reason": reason,
        "message": PRIVATE,
        "metadata": {"project": PRIVATE, "service": PRIVATE},
    }
    if structured:
        entry["@type"] = "type.googleapis.com/google.rpc.ErrorInfo"
    return {
        "error": {
            "message": PRIVATE,
            "details" if structured else "errors": [entry],
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,reason,structured,expected",
    [
        (401, "authError", False, "google_authentication_failed"),
        (403, "accessNotConfigured", False, "google_api_disabled"),
        (403, "SERVICE_DISABLED", True, "google_api_disabled"),
        (403, "insufficientPermissions", False, "google_scope_missing"),
        (403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT", True, "google_scope_missing"),
        (403, "rateLimitExceeded", False, "google_rate_limited"),
        (403, "userRateLimitExceeded", False, "google_rate_limited"),
        (403, "dailyLimitExceeded", False, "google_daily_limit_exceeded"),
        (403, "quotaExceeded", False, "google_quota_exceeded"),
        (403, "QUOTA_EXCEEDED", True, "google_quota_exceeded"),
        (403, "RESOURCE_QUOTA_EXCEEDED", True, "google_quota_exceeded"),
        (429, "QUOTA_EXCEEDED", True, "google_quota_exceeded"),
        (403, "RATE_LIMIT_EXCEEDED", True, "google_rate_limited"),
        (403, "forbidden", False, "google_permission_denied"),
        (403, PRIVATE, False, "google_permission_denied"),
        (429, PRIVATE, False, "google_rate_limited"),
        (503, PRIVATE, False, "google_provider_unavailable"),
        (400, PRIVATE, False, "google_source_error"),
    ],
)
async def test_google_failures_have_fixed_diagnostics_without_provider_details(
    status: int, reason: str, structured: bool, expected: str
) -> None:
    gateway = GoogleSourceGateway(sleep=no_wait)
    response = httpx.Response(status, json=provider_error(reason, structured=structured))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(GoogleSourceError) as caught:
            await gateway._get(client, "https://gmail.googleapis.com/" + PRIVATE)
    error = caught.value
    diagnostic: dict[str, str | int] = {"code": expected, "http_status": status}
    if expected in {"google_rate_limited", "google_provider_unavailable"}:
        diagnostic["retry_after_seconds"] = 60
    if expected in {"google_daily_limit_exceeded", "google_quota_exceeded"}:
        diagnostic["retry_after_seconds"] = 300
    assert error.diagnostic() == diagnostic
    assert isinstance(error, GoogleSourceAuthorizationError) == (status in {401, 403})
    assert PRIVATE not in str(error)
    assert PRIVATE not in json.dumps(error.diagnostic())
    assert PRIVATE not in repr(vars(error))
    assert error.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"error": "SERVICE_DISABLED"},
        {"error": {"message": "SERVICE_DISABLED"}},
        {"error": {"errors": [{"reason": {"SERVICE_DISABLED": True}}]}},
        {"error": {"details": [{"reason": "SERVICE_DISABLED", "@type": "unknown"}]}},
    ],
)
async def test_unknown_or_malformed_403_details_are_not_inferred_from_prose(payload: Any) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json=payload))
    ) as client:
        with pytest.raises(GoogleSourceAuthorizationError) as caught:
            await GoogleSourceGateway()._get(client, "https://gmail.googleapis.com/test")
    assert caught.value.diagnostic() == {
        "code": "google_permission_denied",
        "http_status": 403,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code", [(200, "google_invalid_response"), (403, "google_permission_denied")]
)
async def test_non_json_responses_do_not_leak_private_body(status: int, code: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, content=PRIVATE.encode()))
    ) as client:
        with pytest.raises(GoogleSourceError) as caught:
            await GoogleSourceGateway()._get(client, "https://gmail.googleapis.com/test")
    assert caught.value.diagnostic() == {"code": code, "http_status": status}
    assert PRIVATE not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
async def test_success_response_must_be_an_object() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[PRIVATE]))
    ) as client:
        with pytest.raises(GoogleSourceError) as caught:
            await GoogleSourceGateway()._get(client, "https://gmail.googleapis.com/test")
    assert caught.value.diagnostic() == {"code": "google_invalid_response", "http_status": 200}


@pytest.mark.asyncio
async def test_transport_failure_has_no_raw_exception_chain_or_http_status() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(PRIVATE, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(GoogleSourceError) as caught:
            await GoogleSourceGateway()._get(client, "https://gmail.googleapis.com/test")
    error = caught.value
    assert error.diagnostic() == {"code": "google_transport_error"}
    assert PRIVATE not in "".join(traceback.format_exception(error))


@pytest.mark.asyncio
async def test_expired_cursor_keeps_existing_reconciliation_control_flow() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(410, json=provider_error(PRIVATE)))
    ) as client:
        with pytest.raises(ExpiredSourceCursor):
            await GoogleSourceGateway()._get(
                client, "https://www.googleapis.com/calendar/test", expired_status=410
            )


def test_diagnostic_constructor_sanitizes_unrecognized_code_and_status() -> None:
    assert GoogleSourceError(code=PRIVATE, http_status=1000).diagnostic() == {
        "code": "google_source_error"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["gmail", "calendar"])
async def test_missing_required_cursor_is_an_invalid_provider_response(source: str) -> None:
    gateway = GoogleSourceGateway(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    )
    with pytest.raises(GoogleSourceError) as caught:
        await gateway.fetch(
            source=source,
            access_token=PRIVATE,
            workspace_id=uuid4(),
            connection_id=uuid4(),
            cursor=None,
        )
    assert caught.value.diagnostic() == {"code": "google_invalid_response"}


def test_invalid_message_metadata_does_not_leak_conversion_error_content() -> None:
    with pytest.raises(GoogleSourceError) as caught:
        gmail_document(
            {"id": "message", "internalDate": PRIVATE},
            workspace_id=uuid4(),
            connection_id=uuid4(),
            now=datetime.now(UTC),
        )
    assert caught.value.diagnostic() == {"code": "google_invalid_response"}
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))
