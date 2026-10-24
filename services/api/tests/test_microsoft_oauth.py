"""Mock-wire Microsoft OAuth checks, never live Microsoft qualification."""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from navox.core.settings import Settings
from navox.providers import microsoft_oauth as oauth

REDIRECT = "https://navox.test/api/v1/student/calendar/microsoft/callback"


def settings(**changes):
    return Settings(
        _env_file=None,
        microsoft_oauth_client_id="client-id",
        microsoft_oauth_client_secret=SecretStr("client-secret"),
        microsoft_oauth_redirect_uri=REDIRECT,
        **changes,
    )


def test_authorization_url_uses_pkce_and_only_calendar_scopes():
    url = oauth.authorization_url("state-1", "verifier-" + "x" * 50, settings())
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "login.microsoftonline.com"
    assert parts.path == "/common/oauth2/v2.0/authorize"
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [oauth.pkce_challenge("verifier-" + "x" * 50)]
    assert query["state"] == ["state-1"]
    scopes = query["scope"][0].split()
    assert "https://graph.microsoft.com/Calendars.Read" in scopes
    assert "offline_access" in scopes
    assert not any("Mail" in scope or "ReadWrite" in scope for scope in scopes)


def test_scheduling_asks_for_calendar_write_only():
    url = oauth.authorization_url("s", "v" * 60, settings(), scheduling=True)
    scopes = parse_qs(urlsplit(url).query)["scope"][0].split()
    assert "https://graph.microsoft.com/Calendars.ReadWrite" in scopes
    assert not any("Mail" in scope for scope in scopes)


def test_tenant_setting_changes_authority():
    url = oauth.authorization_url("s", "v" * 60, settings(microsoft_oauth_tenant="organizations"))
    assert urlsplit(url).path == "/organizations/oauth2/v2.0/authorize"


def test_unconfigured_deployment_refuses():
    with pytest.raises(oauth.MicrosoftOAuthError) as caught:
        oauth.authorization_url("s", "v" * 60, Settings(_env_file=None))
    assert caught.value.reason == "not_configured"


def test_school_it_approval_is_reported_separately():
    error = oauth.callback_error("access_denied", "AADSTS65001: The user or admin has not...")
    assert error is not None and error.reason == "admin_consent_required"
    assert oauth.callback_error("consent_required", None).reason == "admin_consent_required"
    assert oauth.callback_error("access_denied", "User cancelled").reason == "failed"
    assert oauth.callback_error(None, None) is None


@pytest.mark.asyncio
async def test_code_exchange_posts_pkce_and_returns_tokens():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "https://graph.microsoft.com/Calendars.Read openid email",
            },
        )

    tokens = await oauth.exchange_authorization_code(
        "code-1", "verifier-1", settings(), transport=httpx.MockTransport(handler)
    )
    assert tokens["access_token"] == "access"
    assert oauth.granted(tokens, scheduling=False)
    assert not oauth.granted(tokens, scheduling=True)
    body = parse_qs(seen[0].content.decode())
    assert seen[0].url.path == "/common/oauth2/v2.0/token"
    assert body["grant_type"] == ["authorization_code"]
    assert body["code_verifier"] == ["verifier-1"]
    assert body["client_secret"] == ["client-secret"]


@pytest.mark.asyncio
async def test_token_errors_never_echo_provider_text():
    async def handler(request):
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "AADSTS65001 private tenant details",
            },
        )

    with pytest.raises(oauth.MicrosoftOAuthError) as caught:
        await oauth.refresh_access_token(
            "refresh", settings(), transport=httpx.MockTransport(handler)
        )
    assert caught.value.reason == "admin_consent_required"
    assert "private tenant" not in str(caught.value)


@pytest.mark.asyncio
async def test_malformed_token_response_fails_closed():
    async def handler(request):
        return httpx.Response(200, json={"access_token": "", "token_type": "Bearer"})

    with pytest.raises(oauth.MicrosoftOAuthError):
        await oauth.exchange_authorization_code(
            "code", "verifier", settings(), transport=httpx.MockTransport(handler)
        )


def test_production_requires_https_secret_and_encryption():
    with pytest.raises(ValueError, match="Microsoft OAuth redirect must use HTTPS"):
        Settings(
            _env_file=None,
            app_environment="production",
            web_origin="https://navox.test",
            microsoft_oauth_client_id="client-id",
            microsoft_oauth_redirect_uri="http://insecure.test/callback",
        )
