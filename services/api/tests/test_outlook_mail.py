"Synthetic Microsoft boundary tests. No test authenticates a real mailbox or sends mail."

import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.models import ConnectorConnection, OAuthAuthorizationAttempt, User, UserSession
from navox.db.outlook import OutlookMailDraft
from navox.db.session import get_database_session
from navox.outlook import service
from navox.providers.microsoft_mail import (
    READ_SCOPES,
    MailIdentity,
    MailMessage,
    MailToken,
    MicrosoftMailError,
    receipt_fingerprint,
)

PASSWORD = "outlook-fixture-password"


class FakeMicrosoft:
    def __init__(self):
        self.allow_send = False
        self.exchange_calls = 0
        self.refresh_calls = 0
        self.read_calls = 0
        self.send_calls = 0
        self.receipt_calls = 0
        self.identity_value = MailIdentity(
            "fixture-pairwise-subject", "fixture-graph-id", "owner@example.com"
        )
        self.scopes = set(READ_SCOPES)
        self.receipt_ready = False
        self.fail_send = False
        self.last_mime = None
        self.after_message = None
        self.after_identity = None
        self.source = MailMessage(
            id="source-one",
            sender="teacher@example.com",
            recipient="teacher@example.com",
            to=("owner@example.com",),
            subject="Office hours",
            body="Could you meet on Tuesday?",
            preview="Could you meet on Tuesday?",
            sent_at=datetime.now(UTC),
            internet_message_id="<source-one@example.com>",
            conversation_id="conversation-one",
            open_url="https://outlook.office.com/mail/read/source-one",
            flagged=False,
            is_draft=False,
        )

    async def token(self, settings, *, code=None, verifier="", refresh=""):
        if code is not None:
            self.exchange_calls += 1
            assert len(verifier) >= 43
        else:
            self.refresh_calls += 1
            assert refresh == "fixture-refresh-token"
        scopes = set(self.scopes)
        if self.allow_send:
            scopes.add("Mail.Send")
        return MailToken("fixture-access-token", "fixture-refresh-token", frozenset(scopes))

    async def identity(self, token):
        assert token == "fixture-access-token"
        if self.after_identity:
            callback, self.after_identity = self.after_identity, None
            await callback()
        return self.identity_value

    async def message(self, token, identifier):
        self.read_calls += 1
        assert identifier == self.source.id
        if self.after_message:
            callback, self.after_message = self.after_message, None
            await callback()
        return self.source

    async def messages(self, token):
        return [self.source], True

    async def send(self, token, mime):
        self.send_calls += 1
        self.last_mime = mime
        if self.fail_send:
            raise MicrosoftMailError()

    async def sent_receipt(self, token, message_id, expected_hash):
        self.receipt_calls += 1
        assert receipt_fingerprint(self.last_mime) == expected_hash
        assert message_id.encode() in self.last_mime
        return "verified-sent-id" if self.receipt_ready else None


@pytest_asyncio.fixture
async def outlook_env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        microsoft_oauth_client_id="fixture-client",
        microsoft_oauth_client_secret="fixture-client-secret",
        connector_secret_encryption_key=Fernet.generate_key().decode(),
    )
    app = create_app()

    async def database():
        async with ai_database() as db:
            yield db

    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    provider = FakeMicrosoft()
    monkeypatch.setattr(service, "MicrosoftMailGateway", lambda: provider)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "display_name": "Outlook fixture",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 201
        account = response.json()
        env = SimpleNamespace(
            client=client,
            factory=ai_database,
            settings=settings,
            app=app,
            provider=provider,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
            headers={
                "Origin": settings.web_origin,
                "X-NavoX-Account": f"{account['workspace']['id']}:{account['id']}",
            },
        )
        yield env


async def start(env, *, connection_id=None, send=False):
    body = {"confirmed": True, "send": send}
    if connection_id:
        body["connection_id"] = connection_id
    response = await env.client.post("/api/v1/outlook/connect", json=body, headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json(), parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][
        0
    ]


async def connect(env, *, send=False):
    _, state = await start(env)
    result = await env.client.get(
        "/api/v1/outlook/callback", params={"state": state, "code": "fixture-code"}
    )
    assert result.status_code == 303, result.text
    response = await env.client.get("/api/v1/outlook/status", headers=env.headers)
    connection_id = response.json()["connections"][0]["id"]
    if send:
        env.provider.allow_send = True
        _, state = await start(env, connection_id=connection_id, send=True)
        result = await env.client.get(
            "/api/v1/outlook/callback", params={"state": state, "code": "fixture-send-code"}
        )
        assert result.status_code == 303, result.text
    return connection_id


async def preview(env, connection_id, *, request_id=None, body="Tuesday at 11 works for me."):
    payload = {
        "request_id": request_id or str(uuid4()),
        "connection_id": connection_id,
        "source_message_id": env.provider.source.id,
        "subject": "Re: Office hours",
        "body": body,
    }
    response = await env.client.post(
        "/api/v1/outlook/drafts/preview", json=payload, headers=env.headers
    )
    assert response.status_code == 200, response.text
    return response.json(), payload


def send_payload(draft, **changes):
    return {
        "request_id": str(uuid4()),
        "expected_revision": draft["revision"],
        "expected_payload_hash": draft["payload_hash"],
        "confirmed": True,
        **changes,
    }


@pytest.mark.asyncio
async def test_unconfigured_outlook_is_honest_and_creates_no_attempt(outlook_env):
    env = outlook_env
    env.settings.microsoft_oauth_client_secret = None
    response = await env.client.get("/api/v1/outlook/status", headers=env.headers)
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["connections"] == []
    response = await env.client.post(
        "/api/v1/outlook/connect", json={"confirmed": True}, headers=env.headers
    )
    assert response.status_code == 503
    assert "private, no-store" == response.headers["cache-control"]
    async with env.factory() as db:
        assert await db.scalar(select(OAuthAuthorizationAttempt.id)) is None
    assert env.provider.exchange_calls == 0


@pytest.mark.asyncio
async def test_browser_owner_and_origin_checks_precede_provider_calls(outlook_env):
    env = outlook_env
    for headers, expected in [
        ({}, 409),
        ({**env.headers, "X-NavoX-Account": str(uuid4())}, 409),
        ({**env.headers, "Authorization": "Bearer fixture"}, 401),
    ]:
        response = await env.client.get("/api/v1/outlook/status", headers=headers)
        assert response.status_code == expected
        assert response.headers["cache-control"] == "private, no-store"
    valid_session_token = env.client.cookies.get(env.settings.session_cookie_name)
    response = await env.client.get(
        "/api/v1/outlook/status",
        headers={**env.headers, "Authorization": f"Bearer {valid_session_token}"},
    )
    assert response.status_code == 403
    response = await env.client.post(
        "/api/v1/outlook/connect",
        json={"confirmed": True},
        headers={"X-NavoX-Account": env.headers["X-NavoX-Account"]},
    )
    assert response.status_code == 403
    assert env.provider.exchange_calls == 0


@pytest.mark.asyncio
async def test_oauth_pkce_state_is_one_shot_and_minimum_read_consent(outlook_env):
    env = outlook_env
    authorization, state = await start(env)
    query = parse_qs(urlsplit(authorization["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["response_type"] == ["code"]
    assert "Mail.Read" in authorization["requested_scopes"]
    assert "Mail.Send" not in authorization["requested_scopes"]
    assert "code_verifier" not in query
    async with env.factory() as db:
        attempt = await db.scalar(select(OAuthAuthorizationAttempt))
        assert attempt.state_hash == sha256(state.encode()).hexdigest()
        challenge = (
            base64.urlsafe_b64encode(sha256(attempt.code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert query["code_challenge"] == [challenge]
        session = await db.scalar(select(UserSession).where(UserSession.user_id == env.user_id))
        assert attempt.purpose == f"outlook:{session.id}"
    response = await env.client.get(
        "/api/v1/outlook/callback", params={"state": state, "code": "fixture"}
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("outlook_connection=connected")
    repeated = await env.client.get(
        "/api/v1/outlook/callback", params={"state": state, "code": "fixture"}
    )
    assert repeated.status_code == 409
    assert env.provider.exchange_calls == 1
    async with env.factory() as db:
        row = await db.scalar(select(ConnectorConnection))
        assert row.external_account_id == env.provider.identity_value.subject
        assert row.config["account_id"] == env.provider.identity_value.account_id
        assert row.authorized_capabilities == ["communication.messages.read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["state", "expired", "session", "owner"])
async def test_oauth_rejects_wrong_state_expiry_session_or_owner_before_exchange(
    outlook_env, change
):
    env = outlook_env
    _, state = await start(env)
    if change == "state":
        state = "incorrect-state-" + state
    elif change == "expired":
        async with env.factory() as db:
            attempt = await db.scalar(select(OAuthAuthorizationAttempt))
            attempt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
    elif change == "session":
        response = await env.client.post(
            "/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200
    else:
        response = await env.client.post(
            "/api/v1/auth/register",
            json={
                "email": "other@example.com",
                "display_name": "Other fixture",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 201
    response = await env.client.get(
        "/api/v1/outlook/callback", params={"state": state, "code": "fixture"}
    )
    assert response.status_code == 409
    assert env.provider.exchange_calls == 0


@pytest.mark.asyncio
async def test_reconnect_requires_same_provider_identity_and_disconnect_cancels_inflight(
    outlook_env,
):
    env = outlook_env
    connection_id = await connect(env)
    _, state = await start(env, connection_id=connection_id)
    original = env.provider.identity_value
    env.provider.identity_value = MailIdentity(
        "different-subject", "different-account", "other@example.com"
    )
    response = await env.client.get(
        "/api/v1/outlook/callback", params={"state": state, "code": "fixture"}
    )
    assert response.status_code == 409
    env.provider.identity_value = original
    _, state = await start(env, connection_id=connection_id)

    async def revoke():
        response = await env.client.delete(
            f"/api/v1/outlook/connections/{connection_id}", headers=env.headers
        )
        assert response.status_code == 200

    env.provider.after_identity = revoke
    response = await env.client.get(
        "/api/v1/outlook/callback", params={"state": state, "code": "fixture"}
    )
    assert response.status_code == 409
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, UUID(connection_id))
        assert row.status == "DISCONNECTED"
        assert row.credential_reference is None


@pytest.mark.asyncio
async def test_read_requires_fresh_grants_and_returns_no_content_after_disconnect(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    response = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages", headers=env.headers
    )
    assert response.status_code == 200
    assert (
        response.json()["messages"][0]["reason"] == "Possible request: “Could you meet on Tuesday?”"
    )
    assert response.json()["has_more"] is True

    async def revoke():
        response = await env.client.delete(
            f"/api/v1/outlook/connections/{connection_id}", headers=env.headers
        )
        assert response.status_code == 200

    env.provider.after_message = revoke
    response = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages/source-one", headers=env.headers
    )
    assert response.status_code == 403
    assert "Tuesday" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_source_read_fails_if_refresh_identity_or_read_scope_changes(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    env.provider.scopes.remove("Mail.Read")
    response = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages/source-one", headers=env.headers
    )
    assert response.status_code == 403
    assert env.provider.read_calls == 0
    env.provider.scopes.add("Mail.Read")
    env.provider.identity_value = MailIdentity("wrong-sub", "wrong-id", "other@example.com")
    response = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages/source-one", headers=env.headers
    )
    assert response.status_code == 403
    assert env.provider.read_calls == 0


@pytest.mark.asyncio
async def test_encrypted_preview_is_idempotent_and_send_permission_is_separate(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    draft, payload = await preview(env, connection_id)
    repeated = await env.client.post(
        "/api/v1/outlook/drafts/preview", json=payload, headers=env.headers
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == draft["id"]
    assert env.provider.read_calls == 1
    async with env.factory() as db:
        stored = await db.get(OutlookMailDraft, UUID(draft["id"]))
        assert "Tuesday" not in stored.encrypted_payload
        assert "teacher@example.com" not in stored.encrypted_payload
        assert stored.payload_hash == draft["payload_hash"]
    collision = await env.client.post(
        "/api/v1/outlook/drafts/preview", json={**payload, "body": "changed"}, headers=env.headers
    )
    assert collision.status_code == 409
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert response.status_code == 403
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_expired_edit_refreshes_review_and_old_hash_cannot_send(outlook_env):
    env = outlook_env
    connection_id = await connect(env, send=True)
    draft, _ = await preview(env, connection_id)
    async with env.factory() as db:
        row = await db.get(OutlookMailDraft, UUID(draft["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert response.status_code == 409
    response = await env.client.patch(
        f"/api/v1/outlook/drafts/{draft['id']}",
        json={
            "expected_revision": 1,
            "subject": "Re: Office hours",
            "body": "Could we meet Wednesday instead?",
        },
        headers=env.headers,
    )
    assert response.status_code == 200, response.text
    revised = response.json()
    assert revised["revision"] == 2
    assert revised["payload_hash"] != draft["payload_hash"]
    assert datetime.fromisoformat(revised["expires_at"]) > datetime.now(UTC)
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert response.status_code == 409
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send",
        json=send_payload(revised, confirmed=False),
        headers=env.headers,
    )
    assert response.status_code == 422
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_send_is_once_and_202_needs_an_exact_read_only_receipt(outlook_env):
    env = outlook_env
    connection_id = await connect(env, send=True)
    draft, _ = await preview(env, connection_id)
    decision = send_payload(draft)
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=decision, headers=env.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "uncertain"
    assert response.json()["verified_at"] is None
    assert env.provider.send_calls == 1
    for retry in (decision, send_payload(draft)):
        response = await env.client.post(
            f"/api/v1/outlook/drafts/{draft['id']}/send", json=retry, headers=env.headers
        )
        assert response.status_code == 200
        assert response.json()["status"] == "uncertain"
    assert env.provider.send_calls == 1
    duplicate = await env.client.post(
        "/api/v1/outlook/drafts/preview",
        json={
            "request_id": str(uuid4()),
            "connection_id": connection_id,
            "source_message_id": "source-one",
            "subject": "Another reply",
            "body": "Do not duplicate",
        },
        headers=env.headers,
    )
    assert duplicate.status_code == 409
    needs_reply = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages", headers=env.headers
    )
    assert needs_reply.json()["messages"] == []
    env.provider.receipt_ready = True
    checked = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/check", json={}, headers=env.headers
    )
    assert checked.status_code == 200
    assert checked.json()["status"] == "completed"
    assert checked.json()["verified_at"] is not None
    assert env.provider.send_calls == 1
    again = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=decision, headers=env.headers
    )
    assert again.json()["status"] == "completed"
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
async def test_ambiguous_transport_failure_is_durably_non_retryable(outlook_env):
    env = outlook_env
    connection_id = await connect(env, send=True)
    draft, _ = await preview(env, connection_id)
    env.provider.fail_send = True
    decision = send_payload(draft)
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=decision, headers=env.headers
    )
    assert response.status_code == 409
    async with env.factory() as db:
        stored = await db.get(OutlookMailDraft, UUID(draft["id"]))
        assert stored.status == "uncertain"
        assert stored.send_request_id == UUID(decision["request_id"])
    retried = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "uncertain"
    assert env.provider.send_calls == 1
    discarded = await env.client.delete(
        f"/api/v1/outlook/drafts/{draft['id']}", headers=env.headers
    )
    assert discarded.status_code == 409


@pytest.mark.asyncio
async def test_changed_source_recipient_pause_and_tampered_payload_cannot_send(outlook_env):
    from dataclasses import replace

    env = outlook_env
    connection_id = await connect(env, send=True)
    draft, _ = await preview(env, connection_id)
    original = env.provider.source
    env.provider.source = replace(original, recipient="changed@example.com")
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert response.status_code == 409
    env.provider.source = original
    async with env.factory() as db:
        user = await db.get(User, env.user_id)
        user.agent_paused = True
        await db.commit()
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert response.status_code == 403
    async with env.factory() as db:
        user = await db.get(User, env.user_id)
        user.agent_paused = False
        row = await db.get(OutlookMailDraft, UUID(draft["id"]))
        row.encrypted_payload = row.encrypted_payload[:-10] + "tamperedxx"
        await db.commit()
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert response.status_code == 409
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_discard_clears_private_review_and_disconnect_works_without_key(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    draft, _ = await preview(env, connection_id)
    response = await env.client.delete(f"/api/v1/outlook/drafts/{draft['id']}", headers=env.headers)
    assert response.status_code == 200
    async with env.factory() as db:
        row = await db.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.encrypted_payload == ""
    env.settings.connector_secret_encryption_key = None
    response = await env.client.delete(
        f"/api/v1/outlook/connections/{connection_id}", headers=env.headers
    )
    assert response.status_code == 200
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, UUID(connection_id))
        assert row.status == "DISCONNECTED"
        assert row.credential_reference is None
    assert env.provider.send_calls == 0
