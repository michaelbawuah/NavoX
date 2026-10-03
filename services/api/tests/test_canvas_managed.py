"""Actual OAuth/state/broker/transport/runtime/Today paths; provider and model fixtures."""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.exceptions import ApplicationError

from navox.api import canvas
from navox.api.main import create_app
from navox.connectors import activities, canvas_oauth
from navox.connectors.jobs import ConnectorSyncWork
from navox.connectors.outbound import ApprovedHTTPSTransport
from navox.core.settings import Settings, get_settings
from navox.db import session as db_session
from navox.db.base import Base
from navox.db.models import (
    Commitment,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorResource,
    OAuthAuthorizationAttempt,
    User,
    WorkspaceMembership,
)
from navox.db.session import get_database_session
from navox.intelligence.extraction import ModelExtractionResponse

ORIGIN = "https://canvas.example.edu"
SECOND_ORIGIN = "https://canvas.second.edu"
REFRESH = "refresh-SENTINEL-123"
ACCESS = "access-SENTINEL-456"
CAPS = list(canvas_oauth.CANVAS_SCOPES)


class Model:
    def __init__(self):
        self.calls = []
        self.hook = None
        self.escalate = False

    async def extract_operational(self, document, *, owner_email=None):
        self.calls.append(document)
        assert (
            REFRESH not in document.model_dump_json() and ACCESS not in document.model_dump_json()
        )
        content = document.content or ""
        observations = []
        if document.source_type == "academic.assignment":
            kind = "completion" if "Submission status: submitted" in content else "task"
            when = re.search(r"\d{4}-\d{2}-\d{2}T[0-9:+Z-]+", content)
            observations = [
                {
                    "observation_type": kind,
                    "action_text": "submit",
                    "object_text": "the report",
                    "confidence": 0.99,
                    "temporal_expression": when[0] if when else None,
                    "evidence": [
                        {
                            "source": "content",
                            "start_char": 0,
                            "end_char": len(content),
                            "text": content,
                        }
                    ],
                }
            ]
        if document.source_type == "calendar_event":
            observations = [
                {
                    "observation_type": "meeting",
                    "object_text": "Review meeting",
                    "confidence": 0.99,
                    "evidence": [
                        {
                            "source": "content",
                            "start_char": 0,
                            "end_char": len(content),
                            "text": content,
                        }
                    ],
                }
            ]
        result = {"schema_version": "operational-extraction.v2", "observations": observations}
        if self.escalate:
            result["granted_permissions"] = ["canvas.submit"]
        if self.hook:
            await self.hook(document)
        return ModelExtractionResponse(result, "fixture", "fixture-v1")


@pytest_asyncio.fixture
async def env(monkeypatch):
    dsn = os.getenv("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"canvas_{uuid4().hex}"
    admin = None
    if dsn.startswith("postgresql"):
        admin = create_async_engine(dsn)
        async with admin.begin() as c:
            await c.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    settings = Settings(
        _env_file=None,
        ai_provider="openai",
        openai_api_key="fixture",
        canvas_base_url=ORIGIN,
        canvas_oauth_client_id="12345",
        canvas_oauth_client_secret="application-SENTINEL",
        connector_secret_encryption_key=Fernet.generate_key().decode(),
    )

    async def session():
        async with factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_database_session] = session
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    monkeypatch.setattr(activities, "get_settings", lambda: settings)
    model = Model()
    monkeypatch.setattr(activities, "build_ai_gateway", lambda _: model)
    queued, calls = [], []
    state = SimpleNamespace(
        uid="42",
        submitted=False,
        next_link=None,
        hook=None,
        fail_path=None,
        fail_code=429,
        reflected=None,
    )
    when = datetime.now(UTC).replace(microsecond=0)
    due = when + timedelta(hours=6)

    async def dispatch(payload, **kwargs):
        queued.append(payload)
        return "fixture-workflow"

    monkeypatch.setattr(canvas, "dispatch_connector_sync", dispatch)

    async def resolver(host):
        assert host in {"canvas.example.edu", "canvas.second.edu"}
        return ["8.8.8.8"]

    async def handle(request):
        calls.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] in {"canvas.example.edu", "canvas.second.edu"}
        path = request.url.path
        if state.hook:
            await state.hook(request)
        if path == "/login/oauth2/token":
            assert request.method == "POST"
            data = parse_qs(request.content.decode())
            expected_secret = (
                "application-SECOND"
                if request.headers["host"] == "canvas.second.edu"
                else "application-SENTINEL"
            )
            assert data["client_secret"] == [expected_secret]
            if data["grant_type"] == ["refresh_token"]:
                assert data["refresh_token"] == [REFRESH]
                assert data["redirect_uri"] == [settings.canvas_oauth_redirect_uri]
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": ACCESS,
                    "refresh_token": REFRESH,
                    "expires_in": 3600,
                    "user": {"id": int(state.uid), "name": "Ignored name"},
                },
            )
        assert request.method == "GET" and request.headers["Authorization"] == f"Bearer {ACCESS}"
        if state.fail_path == path:
            return httpx.Response(
                state.fail_code, json={"error": "PRIVATE-SENTINEL"}, headers={"Retry-After": "30"}
            )
        if path == "/api/v1/courses":
            return httpx.Response(200, json=[{"id": 7, "name": "Course information"}])
        if path == "/api/v1/courses/7/assignments":
            headers = {"Link": f'<{state.next_link}>; rel="next"'} if state.next_link else {}
            return httpx.Response(
                200,
                headers=headers,
                json=[
                    {
                        "id": 9,
                        "name": "Submit report",
                        "description": state.reflected or "<p>Please submit the report.</p>",
                        "published": True,
                        "due_at": due.isoformat(),
                        "updated_at": when.isoformat(),
                        "html_url": ORIGIN + "/courses/7/assignments/9",
                        "submission": {
                            "workflow_state": "submitted" if state.submitted else "unsubmitted",
                            "updated_at": (
                                when + timedelta(minutes=1) if state.submitted else when
                            ).isoformat(),
                            "grade": "UNMAPPED-GRADE-SENTINEL",
                        },
                    }
                ],
            )
        if path == "/api/v1/announcements":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 11,
                        "title": "Newsletter",
                        "message": "Optional news.",
                        "posted_at": when.isoformat(),
                    }
                ],
            )
        if path == "/api/v1/calendar_events":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 12,
                        "title": "Review meeting",
                        "description": "Review session.",
                        "start_at": due.isoformat(),
                        "end_at": (due + timedelta(hours=1)).isoformat(),
                        "updated_at": when.isoformat(),
                    }
                ],
            )
        raise AssertionError(path)

    def client(origin):
        return httpx.AsyncClient(
            transport=ApprovedHTTPSTransport(
                origin, resolver=resolver, transport=httpx.MockTransport(handle)
            ),
            trust_env=False,
        )

    monkeypatch.setattr(canvas_oauth, "safe_client", client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "canvas-owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Owner",
            },
        )
        assert r.status_code == 201
        account = r.json()
        uid, wid = UUID(account["id"]), UUID(account["workspace"]["id"])
        async with factory() as db:
            user = await db.get(User, uid)
            user.timezone = "UTC"
            await db.commit()
        yield SimpleNamespace(
            client=client,
            factory=factory,
            settings=settings,
            model=model,
            queued=queued,
            calls=calls,
            provider=state,
            uid=uid,
            wid=wid,
            due=due,
        )
    await engine.dispose()
    if admin:
        async with admin.begin() as c:
            await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def begin(env, capabilities=None, institution_id=None):
    r = await env.client.post(
        "/api/v1/connectors/canvas-lms/connect",
        json={
            "request_id": str(uuid4()),
            "confirmed": True,
            "capabilities": CAPS if capabilities is None else capabilities,
            **({"institution_id": institution_id} if institution_id else {}),
        },
    )
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    return parse_qs(urlsplit(url).query)["state"][0], url


def enable_two_schools(env):
    env.settings.canvas_oauth_deployments = [
        {
            "id": "first",
            "name": "First University",
            "origin": ORIGIN,
            "client_id": "12345",
            "client_secret": "application-SENTINEL",
        },
        {
            "id": "second",
            "name": "Second College",
            "origin": SECOND_ORIGIN,
            "client_id": "67890",
            "client_secret": "application-SECOND",
        },
    ]


@pytest.mark.asyncio
async def test_multi_school_catalog_requires_explicit_selection_and_hides_secrets(env):
    enable_two_schools(env)
    response = await env.client.get("/api/v1/connectors/canvas-lms/setup")
    assert response.status_code == 200
    assert response.json()["institutions"] == [
        {"id": "first", "name": "First University", "origin": ORIGIN},
        {"id": "second", "name": "Second College", "origin": SECOND_ORIGIN},
    ]
    assert "application-SENTINEL" not in response.text
    assert "application-SECOND" not in response.text
    command = {"request_id": str(uuid4()), "confirmed": True, "capabilities": CAPS}
    missing = await env.client.post("/api/v1/connectors/canvas-lms/connect", json=command)
    unknown = await env.client.post(
        "/api/v1/connectors/canvas-lms/connect",
        json={**command, "institution_id": "unknown"},
    )
    assert missing.status_code == 400 and unknown.status_code == 400
    assert not env.calls
    _, url = await begin(env, institution_id="second")
    assert urlsplit(url).netloc == "canvas.second.edu"
    assert parse_qs(urlsplit(url).query)["client_id"] == ["67890"]


@pytest.mark.asyncio
async def test_multi_school_callback_binds_selected_school_and_separates_same_user_id(env):
    enable_two_schools(env)
    first_state, _ = await begin(env, institution_id="first")
    second_state, _ = await begin(env, institution_id="second")
    callback = "/api/v1/connectors/canvas-lms/callback"
    for state in (first_state, second_state):
        response = await env.client.get(callback, params={"state": state, "code": "fixture"})
        assert response.status_code == 303, response.text
    async with env.factory() as db:
        rows = list(
            await db.scalars(
                select(ConnectorConnection).where(ConnectorConnection.provider == "canvas")
            )
        )
        assert len(rows) == 2
        assert {row.external_account_id for row in rows} == {f"{ORIGIN}:42", f"{SECOND_ORIGIN}:42"}
        assert rows[0].id != rows[1].id
    assert {request.headers["host"] for request in env.calls} == {
        "canvas.example.edu",
        "canvas.second.edu",
    }
    replay = await env.client.get(callback, params={"state": first_state, "code": "fixture"})
    assert replay.status_code == 400


@pytest.mark.asyncio
async def test_multi_school_rotation_and_reconnect_are_bound_to_original_school(env):
    enable_two_schools(env)
    state, _ = await begin(env, institution_id="second")
    env.settings.canvas_oauth_deployments[1]["client_secret"] = "rotated-secret"
    blocked = await env.client.get(
        "/api/v1/connectors/canvas-lms/callback", params={"state": state, "code": "fixture"}
    )
    assert blocked.status_code == 400 and not env.calls
    env.settings.canvas_oauth_deployments[1]["client_secret"] = "application-SECOND"
    completed = await env.client.get(
        "/api/v1/connectors/canvas-lms/callback", params={"state": state, "code": "fixture"}
    )
    assert completed.status_code == 303
    async with env.factory() as db:
        row = await db.scalar(
            select(ConnectorConnection).where(ConnectorConnection.provider == "canvas")
        )
        identifier = row.id
    reconnect = await env.client.post(
        f"/api/v1/connections/{identifier}/reauthorize",
        json={"request_id": str(uuid4())},
    )
    assert reconnect.status_code == 200, reconnect.text
    assert urlsplit(reconnect.json()["authorization_url"]).netloc == "canvas.second.edu"
    env.settings.canvas_oauth_deployments[1]["client_secret"] = "rotated-secret"
    blocked_reconnect = await env.client.post(
        f"/api/v1/connections/{identifier}/reauthorize",
        json={"request_id": str(uuid4())},
    )
    assert blocked_reconnect.status_code == 409


async def connected(env, capabilities=None):
    state, _ = await begin(env, capabilities)
    r = await env.client.get(
        "/api/v1/connectors/canvas-lms/callback", params={"state": state, "code": "fixture-code"}
    )
    assert r.status_code == 303, r.text
    async with env.factory() as db:
        row = await db.scalar(
            select(ConnectorConnection).where(ConnectorConnection.provider == "canvas")
        )
        return row.id


@pytest.mark.asyncio
async def test_canvas_full_oauth_runtime_today_replay_and_private_storage(env):
    identifier = await connected(env)
    assert len(env.queued) == 1 and not env.model.calls
    await activities._connector_sync(env.queued[0])
    count = len(env.model.calls)
    assert count == 4
    await activities._connector_sync(env.queued[0])
    await activities._connector_sync(
        ConnectorSyncWork(str(identifier), str(env.uid), str(env.wid), str(uuid4()))
    )
    assert len(env.model.calls) == count
    today = await env.client.get("/api/v1/today")
    assert today.status_code == 200 and "report" in today.text.lower(), today.text
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, identifier)
        assert row.last_synced_at is not None and row.sync_cursor is None
        credential = await db.get(ConnectionCredential, row.credential_reference)
        assert REFRESH not in credential.encrypted_refresh_token
        registry = list(await db.scalars(select(ConnectorResource)))
        assert len(registry) == 4
        serialized = json.dumps([r.canonical for r in registry])
        assert "Please submit" not in serialized and "UNMAPPED-GRADE" not in serialized
    assert all("UNMAPPED-GRADE" not in d.model_dump_json() for d in env.model.calls)
    listing = await env.client.get("/api/v1/connections")
    assert listing.status_code == 200 and "Canvas academic context" in listing.text
    assert REFRESH not in listing.text and ACCESS not in listing.text


@pytest.mark.asyncio
async def test_consent_redirect_scopes_and_no_model_during_start(env):
    state, url = await begin(env, ["academic.courses.read", "academic.assignments.read"])
    scopes = parse_qs(urlsplit(url).query)["scope"][0]
    assert "url:GET|/api/v1/courses" in scopes and "url:POST" not in scopes
    assert not env.calls and not env.queued and not env.model.calls
    async with env.factory() as db:
        attempt = await db.scalar(select(OAuthAuthorizationAttempt))
        assert attempt.state_hash != state and "application-SENTINEL" not in attempt.code_verifier


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capabilities",
    [
        ["canvas.submit"],
        ["academic.submissions.read"],
        ["academic.courses.read", "academic.submissions.read"],
        [],
    ],
)
async def test_invalid_permissions_fail_before_provider_io(env, capabilities):
    r = await env.client.post(
        "/api/v1/connectors/canvas-lms/connect",
        json={"request_id": str(uuid4()), "confirmed": True, "capabilities": capabilities},
    )
    assert r.status_code == 422 and not env.calls


@pytest.mark.asyncio
async def test_wrong_origin_and_missing_explicit_consent(env):
    for confirmed in (False, "true", 1):
        r = await env.client.post(
            "/api/v1/connectors/canvas-lms/connect",
            json={"request_id": str(uuid4()), "confirmed": confirmed, "capabilities": CAPS},
        )
        assert r.status_code == 422
    r = await env.client.post(
        "/api/v1/connectors/canvas-lms/connect",
        headers={"Origin": "https://evil.example"},
        json={"request_id": str(uuid4()), "confirmed": True, "capabilities": CAPS},
    )
    assert r.status_code == 403 and not env.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["expired", "used", "other-owner", "config-changed"])
async def test_state_and_configuration_boundaries_block_exchange(env, mode):
    state, _ = await begin(env)
    async with env.factory() as db:
        attempt = await db.scalar(select(OAuthAuthorizationAttempt))
        if mode == "expired":
            attempt.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        elif mode == "used":
            attempt.used_at = datetime.now(UTC)
        elif mode == "other-owner":
            other = User(email=f"other-{uuid4().hex}@example.com", timezone="UTC")
            db.add(other)
            await db.flush()
            attempt.user_id = other.id
        else:
            env.settings.canvas_oauth_client_id = "54321"
        await db.commit()
    r = await env.client.get(
        "/api/v1/connectors/canvas-lms/callback", params={"state": state, "code": "fixture"}
    )
    assert r.status_code == 400 and not env.calls


@pytest.mark.asyncio
async def test_callback_replay_never_exchanges_twice(env):
    state, _ = await begin(env)
    path = "/api/v1/connectors/canvas-lms/callback"
    assert (
        await env.client.get(path, params={"state": state, "code": "fixture"})
    ).status_code == 303
    before = len(env.calls)
    assert (
        await env.client.get(path, params={"state": state, "code": "fixture"})
    ).status_code == 400
    assert len(env.calls) == before


@pytest.mark.asyncio
async def test_pause_resume_and_reconnect_preserve_permissions(env):
    identifier = await connected(env, ["academic.courses.read", "academic.assignments.read"])
    assert (
        await env.client.post(
            f"/api/v1/connections/{identifier}/pause", json={"request_id": str(uuid4())}
        )
    ).status_code == 200
    before = len(env.calls)
    assert await activities._connector_sync(env.queued[0]) == 0
    assert len(env.calls) == before
    response = await env.client.post(
        f"/api/v1/connections/{identifier}/reauthorize", json={"request_id": str(uuid4())}
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert "announcements" not in query["scope"][0]
    response = await env.client.get(
        "/api/v1/connectors/canvas-lms/callback",
        params={"state": query["state"][0], "code": "fixture"},
    )
    assert response.status_code == 303, response.text
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, identifier)
        assert row.status == "PAUSED"
    assert len(env.queued) == 1


@pytest.mark.asyncio
async def test_wrong_canvas_account_cannot_replace_existing_reconnect(env):
    identifier = await connected(env)
    response = await env.client.post(
        f"/api/v1/connections/{identifier}/reauthorize", json={"request_id": str(uuid4())}
    )
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    env.provider.uid = "99"
    response = await env.client.get(
        "/api/v1/connectors/canvas-lms/callback", params={"state": state, "code": "fixture"}
    )
    assert response.status_code == 409
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorConnection)) == 1


@pytest.mark.asyncio
async def test_revocation_during_refresh_prevents_following_resource_read(env):
    identifier = await connected(env)

    async def revoke(request):
        if request.url.path == "/login/oauth2/token":
            async with env.factory() as db:
                row = await db.get(ConnectorConnection, identifier)
                row.authorized_capabilities = []
                await db.commit()

    env.provider.hook = revoke
    before = len(env.calls)
    with pytest.raises(ApplicationError):
        await activities._connector_sync(env.queued[0])
    assert len(env.calls) == before + 1 and not env.model.calls


@pytest.mark.asyncio
async def test_revocation_during_model_io_cannot_accept_assignment(env):
    identifier = await connected(env)

    async def revoke(document):
        if document.source_type == "academic.assignment":
            async with env.factory() as db:
                row = await db.get(ConnectorConnection, identifier)
                row.authorized_capabilities = []
                await db.commit()

    env.model.hook = revoke
    with pytest.raises(ApplicationError):
        await activities._connector_sync(env.queued[0])
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 0
        row = await db.get(ConnectorConnection, identifier)
        assert row.last_synced_at is None


@pytest.mark.asyncio
async def test_permission_subset_does_not_request_submissions_or_other_endpoints(env):
    await connected(env, ["academic.courses.read", "academic.assignments.read"])
    await activities._connector_sync(env.queued[0])
    paths = [r.url.path for r in env.calls]
    assert "/api/v1/announcements" not in paths and "/api/v1/calendar_events" not in paths
    for r in env.calls:
        if "assignments" in r.url.path:
            assert "include[]" not in r.url.params
    assert all("Submission status" not in (d.content or "") for d in env.model.calls)


@pytest.mark.asyncio
async def test_cross_origin_pagination_is_rejected_and_runtime_can_resume(env):
    identifier = await connected(env)
    env.provider.next_link = "https://evil.example/api/v1/courses/7/assignments?page=2"
    with pytest.raises(ApplicationError):
        await activities._connector_sync(env.queued[0])
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, identifier)
        assert row.last_synced_at is None
    assert all(r.headers["host"] == "canvas.example.edu" for r in env.calls)
    env.provider.next_link = None
    # A rejected security response is non-retryable; an explicit new scan is
    # required, while already accepted revisions still reuse their receipts.
    await activities._connector_sync(
        ConnectorSyncWork(str(identifier), str(env.uid), str(env.wid), str(uuid4()))
    )
    assert sum(d.source_type == "academic.course" for d in env.model.calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_is_persisted_without_private_response(env):
    identifier = await connected(env)
    env.provider.fail_path = "/api/v1/courses/7/assignments"
    with pytest.raises(ApplicationError) as error:
        await activities._connector_sync(env.queued[0])
    assert "PRIVATE-SENTINEL" not in str(error.value)
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, identifier)
        assert row.retry_not_before is not None
    before = len(env.calls)
    with pytest.raises(ApplicationError):
        await activities._connector_sync(env.queued[0])
    assert len(env.calls) == before


@pytest.mark.asyncio
async def test_setup_is_not_advertised_without_institution_configuration(env):
    env.settings.canvas_oauth_client_secret = None
    response = await env.client.get("/api/v1/connectors")
    canvas_card = next(c for c in response.json() if c["id"] == "canvas-lms")
    assert canvas_card["availability"] == "setup_pending"
    assert (await env.client.get("/api/v1/connectors/canvas-lms/setup")).status_code == 503


@pytest.mark.asyncio
async def test_submission_state_updates_are_source_context_not_fabricated_completion(env):
    identifier = await connected(env)
    await activities._connector_sync(env.queued[0])
    env.provider.submitted = True
    await activities._connector_sync(
        ConnectorSyncWork(str(identifier), str(env.uid), str(env.wid), str(uuid4()))
    )
    assignments = [d for d in env.model.calls if d.source_type == "academic.assignment"]
    assert len(assignments) == 2
    assert "Submission status: submitted" in assignments[-1].content
    assert assignments[-1].occurred_at > assignments[0].occurred_at
    # SPEC-002 deliberately doesn't treat arbitrary source completion text as
    # the user's completion. Never fake Gmail SENT provenance for Canvas.
    async with env.factory() as db:
        commitments = list(await db.scalars(select(Commitment)))
        assert all(c.status != "completed" for c in commitments)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "workspace"])
async def test_canvas_wrong_owner_or_workspace_prevents_all_io(env, change):
    identifier = await connected(env)
    before = len(env.calls)
    payload = ConnectorSyncWork(
        str(identifier),
        str(uuid4() if change == "owner" else env.uid),
        str(uuid4() if change == "workspace" else env.wid),
        str(uuid4()),
    )
    with pytest.raises(ApplicationError):
        await activities._connector_sync(payload)
    assert len(env.calls) == before and not env.model.calls


@pytest.mark.asyncio
async def test_model_cannot_grant_canvas_write_capabilities(env):
    from navox.db.models import Action, Approval

    identifier = await connected(env)
    env.model.escalate = True
    await activities._connector_sync(env.queued[0])
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Action)) == 0
        assert await db.scalar(select(func.count()).select_from(Approval)) == 0
        row = await db.get(ConnectorConnection, identifier)
        assert set(row.authorized_capabilities) == set(CAPS)


@pytest.mark.asyncio
async def test_membership_revocation_during_oauth_exchange_discards_credentials(env):
    state, _ = await begin(env)

    async def revoke(request):
        if request.url.path == "/login/oauth2/token":
            async with env.factory() as db:
                member = await db.get(WorkspaceMembership, (env.wid, env.uid))
                await db.delete(member)
                await db.commit()

    env.provider.hook = revoke
    result = await env.client.get(
        "/api/v1/connectors/canvas-lms/callback", params={"state": state, "code": "fixture"}
    )
    assert result.status_code == 403
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectorConnection)) == 0


@pytest.mark.asyncio
async def test_catalog_detail_and_listing_agree_on_oauth_availability(env):
    listed = (await env.client.get("/api/v1/connectors")).json()
    detail = await env.client.get("/api/v1/connectors/canvas-lms")
    assert detail.status_code == 200
    assert detail.json() == next(c for c in listed if c["id"] == "canvas-lms")
    assert detail.json()["availability"] == "available"
    env.settings.canvas_oauth_client_secret = None
    assert (await env.client.get("/api/v1/connectors/canvas-lms")).json()[
        "availability"
    ] == "setup_pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("reflected", [REFRESH, ACCESS, "application-SENTINEL"])
async def test_reflected_credentials_never_enter_intelligence_or_registry(env, reflected):
    await connected(env)
    env.provider.reflected = reflected
    with pytest.raises(ApplicationError):
        await activities._connector_sync(env.queued[0])
    assert not any(d.source_type == "academic.assignment" for d in env.model.calls)
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 0
        resources = list(await db.scalars(select(ConnectorResource)))
        assert reflected not in json.dumps([r.canonical for r in resources])


@pytest.mark.asyncio
async def test_approved_scope_filters_and_pagination_survive_http_transport(env):
    await connected(env)
    env.provider.next_link = ORIGIN + "/api/v1/courses/7/assignments?page=2&include[]=all"

    async def second_page(request):
        if request.url.path.endswith("/assignments") and request.url.params.get("page") == "2":
            env.provider.next_link = None

    env.provider.hook = second_page
    await activities._connector_sync(env.queued[0])
    courses = [r for r in env.calls if r.url.path == "/api/v1/courses"]
    assert all(r.url.params["enrollment_type"] == "student" for r in courses)
    assignments = [r for r in env.calls if r.url.path.endswith("/assignments")]
    assert len(assignments) == 2
    assert assignments[1].url.params["page"] == "2"
    assert all(r.url.params.get_list("include[]") == ["submission"] for r in assignments)
    assert all(r.url.params["order_by"] == "id" for r in assignments)
    calendar_reads = [r for r in env.calls if r.url.path == "/api/v1/calendar_events"]
    assert calendar_reads[0].url.params.get_list("context_codes[]") == ["course_7"]
    assert calendar_reads[0].url.params["start_date"] < calendar_reads[0].url.params["end_date"]
    assert sum(d.source_type == "academic.assignment" for d in env.model.calls) == 1


@pytest.mark.asyncio
async def test_manual_canvas_sync_control_dispatches_only_authorized_source(env):
    identifier = await connected(env)
    result = await env.client.post(
        f"/api/v1/connections/{identifier}/sync",
        json={"request_id": str(uuid4()), "source": "canvas"},
    )
    assert result.status_code == 202 and result.json()["dispatch_status"] == "queued"
    assert len(env.queued) == 2
    await env.client.post(
        f"/api/v1/connections/{identifier}/pause", json={"request_id": str(uuid4())}
    )
    result = await env.client.post(
        f"/api/v1/connections/{identifier}/sync",
        json={"request_id": str(uuid4()), "source": "canvas"},
    )
    assert result.status_code == 409 and len(env.queued) == 2
