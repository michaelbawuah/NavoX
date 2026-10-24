import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import http_ece
import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select

from navox.api import student_alerts
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.models import User, UserSession
from navox.db.session import get_database_session
from navox.db.student_alerts import (
    StudentAlertOutbox,
    StudentAlertPreference,
    StudentPushSubscription,
)
from navox.student.schemas import Occurrence
from navox.student_alerts import activities
from navox.student_alerts.activities import prepare_delivery, queue_owner
from navox.student_alerts.push import configured, send_push
from navox.student_alerts.schemas import AlertSettings, SubscriptionInput
from navox.student_alerts.service import (
    AlertError,
    clear_student_alert_data,
    quiet_now,
    reveal_subscription,
    save_settings,
)


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def push_settings(**changes):
    key = ec.generate_private_key(ec.SECP256R1())
    return Settings(
        _env_file=None,
        app_environment="test",
        student_push_enabled=True,
        student_push_vapid_private_key=SecretStr(
            b64(key.private_numbers().private_value.to_bytes(32, "big"))
        ),
        student_push_vapid_public_key=b64(
            key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        ),
        student_push_vapid_subject="mailto:hello@example.com",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
        **changes,
    )


def browser_subscription(suffix="device"):
    key = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    return (
        SubscriptionInput(
            endpoint=f"https://fcm.googleapis.com/fcm/send/{suffix}",
            keys={
                "p256dh": b64(
                    key.public_key().public_bytes(
                        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                    )
                ),
                "auth": b64(auth),
            },
        ),
        key,
        auth,
    )


@pytest_asyncio.fixture
async def env(ai_database):
    settings = push_settings()

    async def database():
        async with ai_database() as session:
            yield session

    app = create_app()
    if not any(
        getattr(route, "path", "") == "/api/v1/student/notifications" for route in app.routes
    ):
        app.include_router(student_alerts.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield SimpleNamespace(client=client, factory=ai_database, settings=settings)


async def register(env, email="student-alerts@example.com"):
    response = await env.client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "twelve-character-password", "display_name": "Student"},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return UUID(data["id"]), UUID(data["workspace"]["id"])


async def setup_owner(env):
    user_id, workspace_id = await register(env)
    subscription, _, _ = browser_subscription()
    response = await env.client.post(
        "/api/v1/student/notifications/subscriptions", json=subscription.model_dump()
    )
    assert response.status_code == 201, response.text
    async with env.factory() as database:
        await save_settings(
            database,
            user_id,
            workspace_id,
            AlertSettings(enabled=True, quiet_start="00:00", quiet_end="00:00"),
        )
    return user_id, workspace_id, UUID(response.json()["id"])


def occurrence(now, *, identifier=None, minutes=15, walk_minutes=None, cancelled=False):
    return Occurrence(
        id=identifier or str(uuid4()),
        meeting_id=uuid4(),
        course_id=uuid4(),
        course_code="CS 2110 PRIVATE",
        course_name="Private class",
        kind="lecture",
        start_at=now + timedelta(minutes=minutes),
        end_at=now + timedelta(minutes=minutes + 50),
        timezone="UTC",
        building="Private building",
        room="Secret room",
        instructor="Private professor",
        walk_minutes=walk_minutes,
        cancelled=cancelled,
        exception=False,
    )


@pytest.mark.asyncio
async def test_every_operation_requires_authentication(env):
    subscription, _, _ = browser_subscription()
    for method, path, body in [
        ("GET", "", None),
        ("PUT", "/settings", {}),
        ("POST", "/subscriptions", subscription.model_dump()),
        ("DELETE", f"/subscriptions/{uuid4()}", None),
    ]:
        response = await env.client.request(
            method, f"/api/v1/student/notifications{path}", json=body
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_is_readonly_and_unconfigured_truthful(env):
    await register(env)
    env.settings.student_push_enabled = False
    for _ in range(2):
        response = await env.client.get("/api/v1/student/notifications")
        assert response.status_code == 200
        assert response.json()["configured"] is False
        assert response.json()["public_key"] is None
        assert response.json()["settings"]["enabled"] is False
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentAlertPreference)) == 0
    assert (
        await env.client.put("/api/v1/student/notifications/settings", json={"enabled": True})
    ).status_code == 503


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/send/x",
        "https://127.0.0.1/send/x",
        "https://fcm.googleapis.com.evil.test/x",
        "https://evilpush.apple.com/x",
        "https://fcm.googleapis.com:8443/x",
        "https://user@fcm.googleapis.com/x",
        "https://fcm.googleapis.com/x?token=secret",
        "https://fcm.googleapis.com/x#secret",
        "https://fcm.googleapis.com/../x",
    ],
)
def test_untrusted_endpoints_rejected(endpoint):
    subscription, _, _ = browser_subscription()
    with pytest.raises(ValidationError):
        SubscriptionInput.model_validate({**subscription.model_dump(), "endpoint": endpoint})


def test_configuration_requires_matching_keys_and_encryption():
    settings = push_settings()
    assert configured(settings)
    settings.connector_secret_encryption_key = None
    settings.google_token_encryption_key = None
    assert not configured(settings)
    settings = push_settings()
    settings.student_push_vapid_public_key = "invalid"
    assert not configured(settings)


@pytest.mark.asyncio
async def test_subscription_encrypted_and_owner_bound_and_delete_scoped(env):
    user_id, workspace_id, subscription_id = await setup_owner(env)
    async with env.factory() as database:
        row = await database.get(StudentPushSubscription, subscription_id)
        assert "fcm.googleapis.com" not in row.encrypted_subscription
        assert "p256dh" not in row.encrypted_subscription
        assert reveal_subscription(row, env.settings).endpoint.endswith("/device")
        row.user_id = uuid4()
        with pytest.raises(AlertError):
            reveal_subscription(row, env.settings)
        await database.rollback()
    env.client.cookies.clear()
    await register(env, "other-alerts@example.com")
    assert (
        await env.client.delete(f"/api/v1/student/notifications/subscriptions/{subscription_id}")
    ).status_code == 404
    subscription, _, _ = browser_subscription()
    assert (
        await env.client.post(
            "/api/v1/student/notifications/subscriptions", json=subscription.model_dump()
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_maximum_five_devices_and_repeated_save_is_idempotent(env):
    await register(env)
    for index in range(5):
        subscription, _, _ = browser_subscription(str(index))
        for _ in range(2):
            response = await env.client.post(
                "/api/v1/student/notifications/subscriptions", json=subscription.model_dump()
            )
            assert response.status_code == 201
    subscription, _, _ = browser_subscription("sixth")
    assert (
        await env.client.post(
            "/api/v1/student/notifications/subscriptions", json=subscription.model_dump()
        )
    ).status_code == 409


@pytest.mark.parametrize(
    ("clock", "quiet"), [("22:59", False), ("23:00", True), ("06:59", True), ("07:00", False)]
)
def test_default_quiet_hours_respect_owner_timezone(clock, quiet):
    now = datetime.fromisoformat(f"2026-10-17T{clock}:00-04:00")
    assert quiet_now(AlertSettings(), "America/New_York", now) is quiet
    assert quiet_now(AlertSettings(), "invalid/zone", now)


@pytest.mark.asyncio
async def test_queue_bundles_within_five_minutes_and_never_duplicates(env, monkeypatch):
    user_id, workspace_id, _ = await setup_owner(env)
    now = datetime.now(UTC)
    rows = [occurrence(now), occurrence(now, minutes=19)]

    async def projection(*args, **kwargs):
        return rows

    monkeypatch.setattr(activities, "list_occurrences", projection)
    async with env.factory() as database:
        assert await queue_owner(database, user_id, workspace_id, now) == 1
        assert await queue_owner(database, user_id, workspace_id, now + timedelta(minutes=1)) == 0
        outbox = await database.scalar(select(StudentAlertOutbox))
        assert len(outbox.occurrences) == 2
        assert "Private" not in json.dumps(outbox.occurrences)
        prepared = await prepare_delivery(database, outbox.id, now, env.settings)
        assert prepared is not None
        assert prepared[2]["url"] == "/classes"
        assert "Private" not in json.dumps(prepared[2])
        assert await prepare_delivery(database, outbox.id, now, env.settings) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel", "move", "pause", "revoke", "disable"])
async def test_delivery_rechecks_changes_before_network(env, monkeypatch, change):
    user_id, workspace_id, subscription_id = await setup_owner(env)
    now = datetime.now(UTC)
    rows = [occurrence(now)]

    async def projection(*args, **kwargs):
        return rows

    monkeypatch.setattr(activities, "list_occurrences", projection)
    async with env.factory() as database:
        await queue_owner(database, user_id, workspace_id, now)
        outbox = await database.scalar(select(StudentAlertOutbox))
        if change == "cancel":
            rows[:] = [rows[0].model_copy(update={"cancelled": True})]
        if change == "move":
            rows[:] = [rows[0].model_copy(update={"start_at": now + timedelta(hours=1)})]
        if change == "pause":
            (await database.get(User, user_id)).agent_paused = True
        if change == "disable":
            (await database.scalar(select(StudentAlertPreference))).enabled = False
        if change == "revoke":
            sub = await database.get(StudentPushSubscription, subscription_id)
            (await database.get(UserSession, sub.session_id)).revoked_at = now
        await database.commit()
        assert await prepare_delivery(database, outbox.id, now, env.settings) is None
        assert outbox.state in {"suppressed", "superseded"}


@pytest.mark.asyncio
async def test_leave_now_requires_manual_walktime_and_edit_invalidates_queue(env, monkeypatch):
    user_id, workspace_id, _ = await setup_owner(env)
    now = datetime.now(UTC)
    rows = [occurrence(now, minutes=12)]

    async def projection(*args, **kwargs):
        return rows

    monkeypatch.setattr(activities, "list_occurrences", projection)
    async with env.factory() as database:
        await save_settings(
            database,
            user_id,
            workspace_id,
            AlertSettings(
                enabled=True,
                next_class=False,
                leave_now=True,
                quiet_start="00:00",
                quiet_end="00:00",
            ),
        )
        assert await queue_owner(database, user_id, workspace_id, now) == 0
        rows[:] = [rows[0].model_copy(update={"walk_minutes": 9})]
        assert await queue_owner(database, user_id, workspace_id, now) == 1
        outbox = await database.scalar(select(StudentAlertOutbox))
        rows[:] = [rows[0].model_copy(update={"walk_minutes": 2})]
        assert await prepare_delivery(database, outbox.id, now, env.settings) is None


@pytest.mark.asyncio
async def test_daily_cap_reserves_attempts_even_when_delivery_unknown(env, monkeypatch):
    user_id, workspace_id, subscription_id = await setup_owner(env)
    now = datetime.now(UTC)
    rows = [occurrence(now)]

    async def projection(*args, **kwargs):
        return rows

    monkeypatch.setattr(activities, "list_occurrences", projection)
    async with env.factory() as database:
        pref = await database.scalar(select(StudentAlertPreference))
        pref.daily_cap = 1
        database.add(
            StudentAlertOutbox(
                user_id=user_id,
                workspace_id=workspace_id,
                subscription_id=subscription_id,
                bundle_key="previous",
                preference_revision=pref.revision,
                occurrences=[],
                scheduled_for=now,
                expires_at=now,
                state="uncertain",
                attempted_at=now,
            )
        )
        await database.commit()
        await queue_owner(database, user_id, workspace_id, now)
        outbox = await database.scalar(
            select(StudentAlertOutbox).where(StudentAlertOutbox.state == "queued")
        )
        assert await prepare_delivery(database, outbox.id, now, env.settings) is None
        assert outbox.state == "capped"


@pytest.mark.asyncio
async def test_reset_removes_only_exact_owner_subscription_and_choices(env):
    user_id, workspace_id, _ = await setup_owner(env)
    env.client.cookies.clear()
    other_user, other_workspace = await register(env, "kept-student@example.com")
    async with env.factory() as database:
        await save_settings(database, other_user, other_workspace, AlertSettings())
        await clear_student_alert_data(database, user_id=user_id, workspace_id=workspace_id)
        await database.commit()
        assert await database.scalar(select(func.count()).select_from(StudentPushSubscription)) == 0
        remaining = await database.scalar(select(StudentAlertPreference))
        assert remaining.user_id == other_user


@pytest.mark.asyncio
async def test_real_rfc8291_encryption_decrypts_and_no_endpoint_logs(caplog):
    subscription, private, auth = browser_subscription("secret-device-capability")
    captured = []

    def receive(request):
        captured.append(request)
        assert request.headers["Content-Encoding"] == "aes128gcm"
        assert request.headers["Authorization"].startswith("vapid t=")
        plaintext = http_ece.decrypt(
            request.content, private_key=private, auth_secret=auth, version="aes128gcm"
        )
        assert json.loads(plaintext)["url"] == "/classes"
        return httpx.Response(201)

    status = await send_push(
        subscription,
        {"title": "NavoX", "body": "Class reminder", "tag": "class-" + "a" * 26, "url": "/classes"},
        push_settings(),
        transport=httpx.MockTransport(receive),
    )
    assert status == 201 and len(captured) == 1
    assert "secret-device-capability" not in caplog.text


@pytest.mark.asyncio
async def test_real_saved_class_projection_and_cancel_before_delivery(env):
    from navox.student.schemas import CourseInput, ExceptionInput, MeetingInput
    from navox.student.service import save_course, save_exception, save_meeting

    user_id, workspace_id, _ = await setup_owner(env)
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    start = (now + timedelta(minutes=15)).replace(second=0, microsecond=0)
    end = start + timedelta(minutes=45)
    async with env.factory() as database:
        course = await save_course(
            database,
            user_id,
            workspace_id,
            CourseInput(
                code="CS 2110",
                name="A real saved class",
                term_start=start.date(),
                term_end=start.date(),
            ),
        )
        meeting = await save_meeting(
            database,
            user_id,
            workspace_id,
            MeetingInput(
                weekdays=[start.weekday()],
                start_time=start.strftime("%H:%M"),
                end_time=end.strftime("%H:%M"),
                timezone="UTC",
                building="Private building",
            ),
            course_id=course.id,
        )
        assert await queue_owner(database, user_id, workspace_id, now) == 1
        outbox = await database.scalar(select(StudentAlertOutbox))
        await save_exception(
            database,
            user_id,
            workspace_id,
            meeting.id,
            start.date(),
            ExceptionInput(revision=meeting.revision, cancelled=True),
        )
        assert await prepare_delivery(database, outbox.id, now, env.settings) is None
        assert outbox.state == "superseded"


@pytest.mark.asyncio
async def test_cancel_after_reservation_still_blocks_provider_io(env, monkeypatch):
    user_id, workspace_id, _ = await setup_owner(env)
    now = datetime.now(UTC)
    rows = [occurrence(now)]
    calls = 0

    async def projection(*args, **kwargs):
        nonlocal calls
        calls += 1
        return rows if calls < 3 else []

    async def forbidden(*args, **kwargs):
        pytest.fail("A cancelled class must not reach push transport")

    monkeypatch.setattr(activities, "list_occurrences", projection)
    monkeypatch.setattr(activities, "send_push", forbidden)
    async with env.factory() as database:
        await queue_owner(database, user_id, workspace_id, now)
        identifier = await database.scalar(select(StudentAlertOutbox.id))
    assert await activities.deliver_one(env.factory, identifier, now, env.settings) == "superseded"
    assert calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [410, "timeout"])
async def test_transport_attempt_is_never_replayed_and_expired_endpoint_removed(
    env, monkeypatch, result
):
    user_id, workspace_id, subscription_id = await setup_owner(env)
    now = datetime.now(UTC)
    rows = [occurrence(now)]

    async def projection(*args, **kwargs):
        return rows

    attempts = []

    async def transport(*args, **kwargs):
        attempts.append(1)
        if result == "timeout":
            raise TimeoutError("endpoint secret must not be stored")
        return result

    monkeypatch.setattr(activities, "list_occurrences", projection)
    monkeypatch.setattr(activities, "send_push", transport)
    async with env.factory() as database:
        await queue_owner(database, user_id, workspace_id, now)
        identifier = await database.scalar(select(StudentAlertOutbox.id))
    assert await activities.deliver_one(env.factory, identifier, now, env.settings) == (
        "expired" if result == 410 else "uncertain"
    )
    assert await activities.deliver_one(env.factory, identifier, now, env.settings) == "skipped"
    assert attempts == [1]
    async with env.factory() as database:
        row = await database.get(StudentAlertOutbox, identifier)
        assert row.attempted_at is not None
        assert row.state in {"expired", "uncertain"}
        assert (await database.get(StudentPushSubscription, subscription_id) is None) is (
            result == 410
        )


@pytest.mark.asyncio
async def test_revoked_subscriptions_cleaned_even_when_push_configuration_disabled(env):
    _, _, subscription_id = await setup_owner(env)
    now = datetime.now(UTC)
    async with env.factory() as database:
        subscription = await database.get(StudentPushSubscription, subscription_id)
        (await database.get(UserSession, subscription.session_id)).revoked_at = now
        await database.commit()
    env.settings.student_push_enabled = False
    assert await activities.reconcile_alerts(env.factory, env.settings, now) == 0
    async with env.factory() as database:
        assert await database.get(StudentPushSubscription, subscription_id) is None


@pytest.mark.asyncio
async def test_quiet_end_does_not_replay_reminder_from_quiet_hours(env, monkeypatch):
    user_id, workspace_id, _ = await setup_owner(env)
    now = datetime.now(UTC).replace(hour=7, minute=0, second=0, microsecond=0)
    rows = [occurrence(now, minutes=14)]  # reminder was due at 06:59, during quiet hours

    async def projection(*args, **kwargs):
        return rows

    monkeypatch.setattr(activities, "list_occurrences", projection)
    async with env.factory() as database:
        await save_settings(database, user_id, workspace_id, AlertSettings(enabled=True))
        assert await queue_owner(database, user_id, workspace_id, now) == 0


@pytest.mark.asyncio
async def test_notification_account_header_rejects_stale_owner_before_read_or_write(env):
    user_id, workspace_id = await register(env)
    wrong = {"X-NavoX-Account": f"{workspace_id}:{uuid4()}"}
    response = await env.client.get("/api/v1/student/notifications", headers=wrong)
    assert response.status_code == 409
    response = await env.client.put(
        "/api/v1/student/notifications/settings",
        headers=wrong,
        json=AlertSettings(enabled=True).model_dump(),
    )
    assert response.status_code == 409
    async with env.factory() as database:
        assert (
            await database.scalar(
                select(StudentAlertPreference).where(StudentAlertPreference.user_id == user_id)
            )
            is None
        )
    response = await env.client.get(
        "/api/v1/student/notifications",
        headers={"X-NavoX-Account": f"{workspace_id}:{user_id}"},
    )
    assert response.status_code == 200
