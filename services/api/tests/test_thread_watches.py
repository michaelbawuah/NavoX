from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from navox.api import thread_watches as api
from navox.api.auth import CurrentAccount, get_current_account
from navox.connectors.builtin.google_gmail import GMAIL_CAPABILITY
from navox.core.settings import Settings, get_settings
from navox.db.models import (
    Action,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    Plan,
    PlanStep,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.session import get_database_session
from navox.db.thread_watches import ThreadWatch
from navox.providers.google_sources import GMAIL_READ_SCOPE
from navox.providers.google_thread_watch import (
    ThreadMessage,
    ThreadMetadata,
    ThreadWatchMetadataError,
)
from navox.thread_watches import service

OWNER = "watch-owner@example.com"
TARGET = "watch-person@example.com"


@pytest_asyncio.fixture
async def watch_env(ai_database, monkeypatch):
    now = datetime.now(UTC)
    settings = Settings(_env_file=None, app_environment="test")
    async with ai_database() as db:
        user = User(email=OWNER, display_name="Watch owner")
        outsider = User(email="other-watch-owner@example.com")
        workspace = Workspace(name="Personal watch fixture", workspace_type="personal")
        credential = ConnectionCredential(encrypted_refresh_token="fixture-only")
        db.add_all([user, outsider, workspace, credential])
        await db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="owner"))
        connection = Connection(
            user_id=user.id,
            workspace_id=workspace.id,
            provider="google",
            external_account_id="watch-fixture",
            external_email=OWNER,
            credential_reference=credential.id,
            granted_scopes=[GMAIL_READ_SCOPE],
            status="active",
        )
        definition = ConnectorDefinition(
            connector_key="google-gmail",
            version="1",
            display_name="Gmail",
            connector_class="builtin",
            trust_level="first_party",
            active=True,
        )
        plan = Plan(
            user_id=user.id,
            workspace_id=workspace.id,
            request_id=uuid4(),
            goal="Send a reviewed synthetic email",
            status="completed",
            context_hash="b" * 64,
        )
        db.add_all([connection, definition, plan])
        await db.flush()
        native = ConnectorConnection(
            connector_definition_id=definition.id,
            legacy_connection_id=connection.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider="google",
            external_account_id="watch-fixture",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[GMAIL_CAPABILITY],
        )
        step = PlanStep(
            plan_id=plan.id,
            sequence_number=1,
            action_type="gmail.send",
            description="Synthetic reviewed send",
            risk_level="R2",
            status="completed",
        )
        db.add_all([native, step])
        await db.flush()
        action = Action(
            user_id=user.id,
            workspace_id=workspace.id,
            plan_step_id=step.id,
            provider="google",
            action_type="gmail.send",
            risk_level="R2",
            status="completed",
            requires_approval=True,
            payload_hash="a" * 64,
            idempotency_key=str(uuid4()),
            payload={
                "connection_id": str(connection.id),
                "sender": OWNER,
                "to": TARGET,
                "subject": "Synthetic follow-up",
                "body_text": "An approved fixture message.",
                "post_send_state": "waiting",
            },
            result={"message_id": "sent_1", "thread_id": "thread_1"},
            executed_at=now - timedelta(hours=3),
            verified_at=now - timedelta(hours=2),
        )
        db.add(action)
        await db.commit()
        env = SimpleNamespace(
            factory=ai_database,
            settings=settings,
            now=now,
            user_id=user.id,
            outsider_id=outsider.id,
            workspace_id=workspace.id,
            connection_id=connection.id,
            native_id=native.id,
            action_id=action.id,
            dispatched=[],
            tokens=[],
        )

    async def database_override():
        async with ai_database() as db:
            yield db

    async def account_override():
        async with ai_database() as db:
            return CurrentAccount(
                await db.get(User, env.user_id), await db.get(Workspace, env.workspace_id)
            )

    async def dispatch(**kwargs):
        env.dispatched.append(kwargs)
        return "fixture-workflow"

    async def token(_db, *, connection, settings):
        env.tokens.append(connection.id)
        return "fixture-token"

    monkeypatch.setattr(api, "dispatch_thread_watch", dispatch)
    monkeypatch.setattr(service, "access_token_for_connection", token)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_account] = account_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        env.client = client
        yield env


async def create_watch(env, **changes):
    request = {
        "action_id": str(env.action_id),
        "request_id": str(uuid4()),
        "remind_at": (env.now + timedelta(hours=3)).isoformat(),
        **changes,
    }
    return await env.client.post("/api/v1/thread-watches", json=request)


async def create_ok(env):
    response = await create_watch(env)
    assert response.status_code == 200, response.text
    return UUID(response.json()["id"])


async def load_watch(env, watch_id):
    async with env.factory() as db:
        return await db.get(ThreadWatch, watch_id)


def metadata(env, *, reply=False, baseline=True):
    sent_at = env.now - timedelta(hours=3)
    messages = [
        ThreadMessage(
            "sent_1" if baseline else "another",
            "thread_1",
            frozenset({"SENT"}),
            sent_at,
            OWNER,
            (TARGET,),
        )
    ]
    if reply:
        messages.append(
            ThreadMessage(
                "reply_1",
                "thread_1",
                frozenset({"INBOX"}),
                sent_at + timedelta(minutes=30),
                TARGET,
                (OWNER,),
            )
        )
    return ThreadMetadata("thread_1", tuple(messages))


class Gateway:
    def __init__(self, result, on_fetch=None):
        self.result = result
        self.on_fetch = on_fetch
        self.calls = []

    async def fetch(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_fetch is not None:
            await self.on_fetch()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def check(env, watch_id, gateway, *, now=None, revision=1):
    async with env.factory() as db:
        return await service.check_watch(
            db,
            watch_id=watch_id,
            revision=revision,
            settings=env.settings,
            gateway=gateway,
            now=now or env.now + timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_create_replays_request_and_same_action_without_duplicate_work(watch_env):
    env = watch_env
    request_id = str(uuid4())
    first = await create_watch(env, request_id=request_id)
    assert first.status_code == 200, first.text
    duplicate = await create_watch(env, request_id=request_id)
    same_action = await create_watch(env, remind_at=(env.now + timedelta(days=1)).isoformat())
    reused_request = await create_watch(env, request_id=request_id, action_id=str(uuid4()))
    assert reused_request.status_code == 409
    assert duplicate.json()["id"] == same_action.json()["id"] == first.json()["id"]
    assert same_action.json()["remind_at"] == first.json()["remind_at"]
    assert len(env.dispatched) == 1
    assert env.tokens == []
    async with env.factory() as db:
        assert len(list(await db.scalars(select(ThreadWatch)))) == 1


@pytest.mark.asyncio
async def test_get_and_list_are_private_read_only_views_without_provider_calls(watch_env):
    env = watch_env
    watch_id = await create_ok(env)
    detail = await env.client.get(f"/api/v1/thread-watches/{watch_id}")
    listing = await env.client.get("/api/v1/thread-watches")
    assert detail.status_code == listing.status_code == 200
    assert detail.headers["Cache-Control"] == listing.headers["Cache-Control"] == "no-store"
    assert listing.json()["items"][0]["id"] == str(watch_id)
    assert detail.json()["status"] == "watching"
    assert detail.json()["last_checked_at"] is None
    assert "body_text" not in detail.text and "payload_hash" not in detail.text
    assert env.tokens == [] and len(env.dispatched) == 1
    async with env.factory() as db:
        watch = await db.get(ThreadWatch, watch_id)
        watch.user_id = env.outsider_id
        await db.commit()
    assert (await env.client.get(f"/api/v1/thread-watches/{watch_id}")).status_code == 404
    assert (await env.client.get("/api/v1/thread-watches")).json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["uncertain", "unverified", "missing_thread", "other_owner"])
async def test_create_requires_owned_verified_send_with_thread(watch_env, corruption):
    env = watch_env
    async with env.factory() as db:
        action = await db.get(Action, env.action_id)
        if corruption == "uncertain":
            action.status = "uncertain"
        elif corruption == "unverified":
            action.verified_at = None
        elif corruption == "missing_thread":
            action.result = {"message_id": "sent_1"}
        else:
            action.user_id = env.outsider_id
        await db.commit()
    response = await create_watch(env)
    assert response.status_code == (404 if corruption == "other_owner" else 409)
    assert response.headers["Cache-Control"] == "no-store"
    assert env.tokens == [] and env.dispatched == []


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["paused", "scope", "native_paused", "member_role"])
async def test_create_requires_current_owner_and_explicit_gmail_read_access(watch_env, restriction):
    env = watch_env
    async with env.factory() as db:
        if restriction == "paused":
            (await db.get(User, env.user_id)).agent_paused = True
        elif restriction == "scope":
            (await db.get(Connection, env.connection_id)).granted_scopes = []
        elif restriction == "native_paused":
            (await db.get(ConnectorConnection, env.native_id)).health_state = "PAUSED"
        else:
            member = await db.get(WorkspaceMembership, (env.workspace_id, env.user_id))
            member.role = "member"
        await db.commit()
    assert (await create_watch(env)).status_code == 403
    assert env.tokens == [] and env.dispatched == []


@pytest.mark.asyncio
async def test_dispatch_outage_is_visible_and_keeps_durable_pending_watch(watch_env, monkeypatch):
    env = watch_env

    async def unavailable(**kwargs):
        raise TimeoutError("fixture dispatcher unavailable")

    monkeypatch.setattr(api, "dispatch_thread_watch", unavailable)
    response = await create_watch(env)
    assert response.status_code == 200
    assert response.json()["status"] == "check_failed"
    assert response.json()["reason"] == "dispatch_failed"
    assert response.json()["last_checked_at"] is None
    assert response.json()["next_check_at"] is not None
    assert env.tokens == []


@pytest.mark.asyncio
async def test_pause_resume_cancel_and_reschedule_fence_old_jobs(watch_env):
    env = watch_env
    watch_id = await create_ok(env)
    paused = await env.client.post(f"/api/v1/thread-watches/{watch_id}/pause")
    assert paused.json()["status"] == "paused"
    gateway = Gateway(metadata(env))
    assert await check(env, watch_id, gateway) == "superseded"
    assert gateway.calls == [] and env.tokens == []
    assert (await env.client.post(f"/api/v1/thread-watches/{watch_id}/check")).status_code == 409
    rescheduled = await env.client.patch(
        f"/api/v1/thread-watches/{watch_id}",
        json={
            "remind_at": (env.now + timedelta(days=2)).isoformat(),
        },
    )
    assert rescheduled.json()["status"] == "paused" and len(env.dispatched) == 1
    resumed = await env.client.post(f"/api/v1/thread-watches/{watch_id}/resume")
    assert resumed.json()["status"] == "watching" and len(env.dispatched) == 2
    assert (await load_watch(env, watch_id)).revision == 4
    cancelled = await env.client.post(f"/api/v1/thread-watches/{watch_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert (await env.client.post(f"/api/v1/thread-watches/{watch_id}/resume")).json()[
        "status"
    ] == "cancelled"
    assert await check(env, watch_id, gateway, revision=4) == "superseded"


@pytest.mark.asyncio
async def test_due_requires_successful_fresh_complete_check_at_reminder(watch_env):
    env = watch_env
    watch_id = await create_ok(env)
    reminder = env.now + timedelta(hours=3)
    gateway = Gateway(metadata(env))
    assert await check(env, watch_id, gateway, now=reminder - timedelta(seconds=1)) == "watching"
    before = await load_watch(env, watch_id)
    assert service.utc(before.last_checked_at) < reminder
    assert await check(env, watch_id, gateway, now=reminder) == "due"
    after = await load_watch(env, watch_id)
    assert service.utc(after.last_checked_at) == reminder
    assert after.reason == "no_reply_at_last_check"
    assert service.utc(after.next_check_at) == reminder + timedelta(hours=6)
    assert len(gateway.calls) == 2
    # A duplicate durable activity must respect the slower post-reminder cadence.
    assert await check(env, watch_id, gateway, now=reminder + timedelta(minutes=5)) == "scheduled"
    assert len(gateway.calls) == 2
    sent = metadata(env).messages[0]
    late_reply = Gateway(
        ThreadMetadata(
            "thread_1",
            (
                sent,
                ThreadMessage(
                    "late_reply",
                    "thread_1",
                    frozenset({"INBOX"}),
                    reminder + timedelta(hours=1),
                    TARGET,
                    (OWNER,),
                ),
            ),
        )
    )
    assert await check(env, watch_id, late_reply, now=reminder + timedelta(hours=6)) == "replied"
    resolved = await load_watch(env, watch_id)
    assert resolved.status == "replied" and resolved.next_check_at is None


@pytest.mark.asyncio
async def test_reply_after_sent_internal_date_but_before_verification_is_detected(watch_env):
    env = watch_env
    watch_id = await create_ok(env)
    gateway = Gateway(metadata(env, reply=True))
    assert await check(env, watch_id, gateway) == "replied"
    saved = await load_watch(env, watch_id)
    assert saved.reply_message_id == "reply_1"
    assert service.utc(saved.replied_at) == env.now - timedelta(hours=2, minutes=30)
    assert saved.next_check_at is None
    assert await check(env, watch_id, gateway) == "superseded"
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["baseline", "not_found", "permission", "quota"])
async def test_failed_or_unauthorized_scan_cannot_publish_due(watch_env, failure):
    env = watch_env
    watch_id = await create_ok(env)
    result = {
        "baseline": metadata(env, baseline=False),
        "not_found": ThreadWatchMetadataError("google_thread_not_found", http_status=404),
        "permission": ThreadWatchMetadataError("google_permission_denied", http_status=403),
        "quota": ThreadWatchMetadataError(
            "google_rate_limited", http_status=403, retry_after_seconds=120
        ),
    }[failure]
    outcome = await check(env, watch_id, Gateway(result), now=env.now + timedelta(hours=3))
    assert outcome == ("needs_access" if failure == "permission" else "check_failed")
    saved = await load_watch(env, watch_id)
    assert saved.last_checked_at is None and saved.reply_message_id is None
    assert saved.next_check_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["paused", "scope", "disconnected", "native_paused"])
async def test_current_access_is_checked_before_oauth_or_gmail(watch_env, restriction):
    env = watch_env
    watch_id = await create_ok(env)
    async with env.factory() as db:
        if restriction == "paused":
            (await db.get(User, env.user_id)).agent_paused = True
        elif restriction == "native_paused":
            (await db.get(ConnectorConnection, env.native_id)).health_state = "PAUSED"
        elif restriction == "scope":
            (await db.get(Connection, env.connection_id)).granted_scopes = []
        else:
            (await db.get(Connection, env.connection_id)).status = "disconnected"
        await db.commit()
    gateway = Gateway(metadata(env))
    assert await check(env, watch_id, gateway) == "needs_access"
    assert env.tokens == [] and gateway.calls == []
    assert (await load_watch(env, watch_id)).last_checked_at is None


@pytest.mark.asyncio
async def test_existing_source_cooldown_stops_oauth_and_gmail(watch_env, monkeypatch):
    env = watch_env
    watch_id = await create_ok(env)

    async def cooldown(*args):
        return 300

    monkeypatch.setattr(service, "source_retry_after", cooldown)
    gateway = Gateway(metadata(env))
    assert await check(env, watch_id, gateway) == "check_failed"
    assert env.tokens == [] and gateway.calls == []
    assert (await load_watch(env, watch_id)).reason == "gmail_temporarily_unavailable"


@pytest.mark.asyncio
async def test_quota_failure_persists_shared_cooldown_for_the_next_attempt(watch_env):
    env = watch_env
    watch_id = await create_ok(env)
    unavailable = Gateway(
        ThreadWatchMetadataError(
            "google_rate_limited",
            http_status=403,
            retry_after_seconds=120,
        )
    )
    assert await check(env, watch_id, unavailable) == "check_failed"
    assert len(env.tokens) == 1
    async with env.factory() as db:
        watch = await db.get(ThreadWatch, watch_id)
        watch.next_check_at = env.now - timedelta(minutes=1)
        await db.commit()
    gateway = Gateway(metadata(env))
    assert await check(env, watch_id, gateway) == "check_failed"
    assert gateway.calls == [] and len(env.tokens) == 1
    assert (await load_watch(env, watch_id)).reason == "gmail_temporarily_unavailable"


@pytest.mark.asyncio
async def test_repeated_check_clicks_do_not_spawn_another_queued_or_fresh_check(watch_env):
    env = watch_env
    watch_id = await create_ok(env)
    initial = await load_watch(env, watch_id)
    for _ in range(2):
        response = await env.client.post(f"/api/v1/thread-watches/{watch_id}/check")
        assert response.status_code == 200
    assert (await load_watch(env, watch_id)).revision == initial.revision
    assert len(env.dispatched) == 1 and env.tokens == []
    async with env.factory() as db:
        watch = await db.get(ThreadWatch, watch_id)
        watch.next_check_at = env.now + timedelta(hours=1)
        watch.last_checked_at = env.now
        await db.commit()
    assert (await env.client.post(f"/api/v1/thread-watches/{watch_id}/check")).status_code == 200
    assert (await load_watch(env, watch_id)).revision == initial.revision
    assert len(env.dispatched) == 1 and env.tokens == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["pause_watch", "cancel_watch", "delete_watch", "disconnect", "pause_navox"]
)
async def test_inflight_revocation_wins_over_successful_provider_response(watch_env, change):
    env = watch_env
    watch_id = await create_ok(env)

    async def revoke():
        if change in {"pause_watch", "cancel_watch"}:
            operation = "pause" if change == "pause_watch" else "cancel"
            response = await env.client.post(f"/api/v1/thread-watches/{watch_id}/{operation}")
            assert response.status_code == 200
            return
        async with env.factory() as db:
            if change == "delete_watch":
                await db.execute(delete(ThreadWatch).where(ThreadWatch.id == watch_id))
            elif change == "disconnect":
                (await db.get(Connection, env.connection_id)).status = "disconnected"
            else:
                (await db.get(User, env.user_id)).agent_paused = True
            await db.commit()

    gateway = Gateway(metadata(env, reply=True), on_fetch=revoke)
    outcome = await check(env, watch_id, gateway, now=env.now + timedelta(hours=3))
    assert outcome == ("needs_access" if change in {"disconnect", "pause_navox"} else "superseded")
    saved = await load_watch(env, watch_id)
    if change == "delete_watch":
        assert saved is None
    else:
        assert saved.last_checked_at is None and saved.reply_message_id is None
        if change == "pause_watch":
            assert saved.status == "paused"
        elif change == "cancel_watch":
            assert saved.status == "cancelled"


@pytest.mark.asyncio
async def test_token_refresh_scope_narrowing_is_persisted_and_prevents_metadata_read(
    watch_env, monkeypatch
):
    env = watch_env
    watch_id = await create_ok(env)

    async def narrow(_db, *, connection, settings):
        env.tokens.append(connection.id)
        connection.granted_scopes = []
        return "fixture-narrowed-token"

    monkeypatch.setattr(service, "access_token_for_connection", narrow)
    gateway = Gateway(metadata(env))
    assert await check(env, watch_id, gateway) == "needs_access"
    assert len(env.tokens) == 1 and gateway.calls == []
    async with env.factory() as db:
        assert (await db.get(Connection, env.connection_id)).granted_scopes == []
    assert (await load_watch(env, watch_id)).last_checked_at is None


@pytest.mark.asyncio
async def test_changed_approved_send_binding_cannot_be_watched(watch_env):
    env = watch_env
    watch_id = await create_ok(env)
    async with env.factory() as db:
        action = await db.get(Action, env.action_id)
        action.payload = {**action.payload, "to": "another-person@example.com"}
        await db.commit()
    gateway = Gateway(metadata(env))
    assert await check(env, watch_id, gateway) == "needs_access"
    assert env.tokens == [] and gateway.calls == []
    assert (await load_watch(env, watch_id)).reason == "send_reference_changed"


@pytest.mark.asyncio
async def test_future_schedule_and_active_lease_do_not_dispatch_duplicate_checks(watch_env):
    env = watch_env
    watch_id = await create_ok(env)
    async with env.factory() as db:
        watch = await db.get(ThreadWatch, watch_id)
        watch.next_check_at = env.now + timedelta(minutes=10)
        await db.commit()
    gateway = Gateway(metadata(env))
    assert await check(env, watch_id, gateway) == "scheduled"
    async with env.factory() as db:
        watch = await db.get(ThreadWatch, watch_id)
        watch.next_check_at = env.now - timedelta(minutes=1)
        watch.lease_until = env.now + timedelta(minutes=2)
        await db.commit()
    assert await check(env, watch_id, gateway) == "checking"
    assert gateway.calls == [] and env.tokens == []
