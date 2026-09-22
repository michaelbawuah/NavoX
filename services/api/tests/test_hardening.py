import json
import logging
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from navox.api.main import app
from navox.core.settings import Settings


def test_api_responses_include_correlation_timing_and_security_headers() -> None:
    request_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    response = TestClient(app).get(
        "/api/v1/health/live",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id
    assert response.headers["server-timing"].startswith("app;dur=")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_invalid_request_id_is_replaced_with_server_generated_uuid() -> None:
    response = TestClient(app).get(
        "/api/v1/health/live",
        headers={"X-Request-ID": "not-a-valid-request-id"},
    )

    assert response.status_code == 200
    generated = response.headers["x-request-id"]
    assert generated != "not-a-valid-request-id"
    assert str(UUID(generated)) == generated


def test_request_log_omits_query_string_and_sensitive_query_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="navox.request")
    secret = "never-log-this-verification-token"

    response = TestClient(app).get(f"/api/v1/health/live?token={secret}")

    assert response.status_code == 200
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "navox.request" and '"event":"http_request"' in record.getMessage()
    ]
    assert messages
    assert all(secret not in message for message in messages)
    event = json.loads(messages[-1])
    assert event["path"] == "/api/v1/health/live"
    assert "query" not in event


def test_cors_preflight_allows_only_declared_api_client_headers() -> None:
    response = TestClient(app).options(
        "/api/v1/health/live",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-request-id,content-type",
        },
    )

    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].casefold()
    assert "authorization" in allowed
    assert "x-request-id" in allowed
    assert "content-type" in allowed


def test_production_configuration_requires_https_web_origin() -> None:
    with pytest.raises(ValidationError, match="Production web origin must use HTTPS"):
        Settings(app_environment="production", web_origin="http://navox.example.com")


def test_production_google_configuration_requires_https_and_protected_tokens() -> None:
    with pytest.raises(ValidationError, match="Production Google OAuth redirect must use HTTPS"):
        Settings(
            app_environment="production",
            web_origin="https://app.navox.example",
            google_oauth_client_id="client-id",
            google_oauth_redirect_uri="http://api.navox.example/callback",
            google_oauth_client_secret="secret",
            google_token_encryption_key="encryption-key",
        )

    with pytest.raises(ValidationError, match="Production Google OAuth requires a client secret"):
        Settings(
            app_environment="production",
            web_origin="https://app.navox.example",
            google_oauth_client_id="client-id",
            google_oauth_redirect_uri="https://api.navox.example/callback",
            google_token_encryption_key="encryption-key",
        )
