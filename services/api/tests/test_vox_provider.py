"""Provider contracts use HTTP fakes; these are not live Cloud qualification."""

import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from uuid import UUID, uuid4

import httpx
import pytest
from livekit.api import TokenVerifier
from pydantic import SecretStr
from sqlalchemy import delete, select
from test_vox_calls import ROOT, start
from test_vox_calls import vox_env as shared_vox_fixture

from navox.ai.features import configured_secrets
from navox.api.vox import media_provider
from navox.core.settings import Settings
from navox.db.models import User
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.db.vox_provision import VoxMediaProvisionAttempt
from navox.vox import admission, media, readiness, service
from navox.vox import provision as attempts
from navox.vox.provider import (
    LiveKitCloudConfig,
    LiveKitCloudProvider,
    MediaUnavailable,
    cloud_config,
)
from navox.vox.reconcile import CleanupClaim, CleanupReceipt, claim_cleanup, finish_cleanup

vox_env = shared_vox_fixture
CONFIG = LiveKitCloudConfig("wss://synthetic.livekit.cloud", "synthetic-key", "s" * 32)


@pytest.fixture(autouse=True)
def model_policy_is_outside_this_transport_contract(monkeypatch):
    # Real registry/policy admission is exercised in test_vox_readiness.py.
    # These transport lifecycle tests intentionally use an isolated provider seam.
    async def already_qualified(*_args, **_kwargs):
        return None

    monkeypatch.setattr(readiness, "require_voice_models", already_qualified)


def intent(*, status="confirmed"):
    now = datetime.now(UTC)
    return media.ConnectionIntent(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        7,
        status,
        now - timedelta(seconds=1),
        now + timedelta(seconds=90),
        "vx-" + uuid4().hex,
        "vc-" + uuid4().hex,
        "va-" + uuid4().hex,
    )


def invalid_intent(change):
    value = intent()
    # Resolve expiry offsets at execution, even after a long collection/shard.
    if isinstance(change.get("expires_at"), timedelta):
        change = {**change, "expires_at": value.created_at + change["expires_at"]}
    return replace(value, **change)


def decode(token):
    header, body, signature = token.split(".")
    expected = hmac.new(CONFIG.api_secret.encode(), (header + "." + body).encode(), hashlib.sha256)
    assert hmac.compare_digest(
        signature, base64.urlsafe_b64encode(expected.digest()).rstrip(b"=").decode()
    )
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def reply(request, data, *, status=200, date=None):
    return httpx.Response(
        status,
        json=data,
        headers={"date": date or format_datetime(datetime.now(UTC), usegmt=True)},
    )


def configured(**changes):
    values = {
        "student_vox_enabled": True,
        "student_vox_media_enabled": True,
        "student_vox_worker_qualified": True,
        "student_vox_livekit_cloud_qualified": True,
        "student_vox_livekit_url": CONFIG.server_url,
        "student_vox_livekit_api_key": SecretStr(CONFIG.api_key),
        "student_vox_livekit_api_secret": SecretStr(CONFIG.api_secret),
    }
    return Settings(_env_file=None, **{**values, **changes})


@pytest.mark.parametrize(
    "gate",
    [
        "student_vox_enabled",
        "student_vox_media_enabled",
        "student_vox_worker_qualified",
        "student_vox_livekit_cloud_qualified",
    ],
)
def test_credentials_never_override_separate_release_qualification(gate):
    with pytest.raises(MediaUnavailable):
        cloud_config(configured(**{gate: False}))
    assert CONFIG.api_secret not in repr(cloud_config(configured()))


@pytest.mark.parametrize(
    "url",
    [
        "ws://synthetic.livekit.cloud",
        "wss://localhost",
        "wss://livekit.cloud.evil.example",
        "wss://synthetic.livekit.cloud:443",
        "wss://user@synthetic.livekit.cloud",
        "wss://synthetic.livekit.cloud/path",
        "wss://synthetic.livekit.cloud?token=private",
        "wss://synthetic.livekit.cloud:not-a-port",
    ],
)
def test_operator_config_requires_cloud_tls_exact_origin(url):
    with pytest.raises(MediaUnavailable):
        cloud_config(configured(student_vox_livekit_url=url))


def test_cleanup_can_run_after_media_kill_switch_without_reopening_admission():
    settings = configured(
        student_vox_enabled=False,
        student_vox_media_enabled=False,
        student_vox_worker_qualified=False,
    )
    assert cloud_config(settings, require_release=False).server_url == CONFIG.server_url
    with pytest.raises(MediaUnavailable):
        cloud_config(settings)


def test_livekit_credentials_participate_in_existing_gateway_secret_exclusion():
    settings = configured()
    protected = configured_secrets(settings)
    assert settings.student_vox_livekit_api_key in protected
    assert settings.student_vox_livekit_api_secret in protected


@pytest.mark.parametrize("role,prefix", [("client", "vc-"), ("agent", "va-")])
def test_signed_grants_are_audio_only_bound_to_immutable_epoch_without_personal_ids(role, prefix):
    value = intent()
    token = LiveKitCloudProvider(CONFIG).participant_token(value, role=role)
    payload = decode(token)
    # Verify interoperability using the actual official SDK as well as our
    # independent HMAC check; neither check represents live-provider acceptance.
    claims = TokenVerifier(CONFIG.api_key, CONFIG.api_secret, leeway=timedelta(0)).verify(token)
    assert claims.identity == payload["sub"]
    assert claims.video.room == value.room_name
    assert claims.video.can_publish_sources == ["microphone"]
    assert claims.video.can_publish_data is False
    assert payload["sub"].startswith(prefix)
    assert payload["exp"] <= value.expires_at.timestamp()
    assert payload["exp"] - payload["nbf"] <= 120
    assert json.loads(payload["metadata"]) == {
        "connection_id": str(value.connection_id),
        "generation": value.generation,
    }
    assert payload["video"] == {
        "room": value.room_name,
        "roomJoin": True,
        "canPublish": True,
        "canPublishSources": ["microphone"],
        "canSubscribe": True,
        "canPublishData": False,
        "canUpdateOwnMetadata": False,
    }
    assert str(value.owner_scope_id) not in json.dumps(payload)
    assert str(value.reservation_id) not in json.dumps(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "preparing"},
        {"status": "revoked"},
        {"room_name": "private-user-email"},
        {"expires_at": timedelta(seconds=-1)},
        {"expires_at": timedelta(minutes=10)},
    ],
)
def test_no_join_token_for_unconfirmed_or_unbounded_connection(change):
    with pytest.raises(MediaUnavailable):
        LiveKitCloudProvider(CONFIG).participant_token(invalid_intent(change))


def test_invalid_join_expirations_stay_fresh_after_delayed_execution(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)

    class Clock:
        @staticmethod
        def now(_timezone):
            return now

    monkeypatch.setitem(globals(), "datetime", Clock)
    monkeypatch.setattr("navox.vox.provider.datetime", Clock)
    for lifetime in (timedelta(seconds=-1), timedelta(minutes=10)):
        before = invalid_intent({"expires_at": lifetime})
        now += timedelta(minutes=9)
        after = invalid_intent({"expires_at": lifetime})
        assert after.created_at - before.created_at == timedelta(minutes=9)
        assert after.expires_at - before.expires_at == timedelta(minutes=9)
        assert after.expires_at - after.created_at == lifetime
        provider = LiveKitCloudProvider(CONFIG)
        assert decode(provider.participant_token(intent()))["exp"] > now.timestamp()
        if lifetime == timedelta(minutes=10):
            # A collection-time expiry has aged into a legitimate short lease.
            assert provider.participant_token(replace(after, expires_at=before.expires_at))
        with pytest.raises(MediaUnavailable):
            provider.participant_token(after)


@pytest.mark.asyncio
async def test_provision_dispatches_named_agent_with_only_opaque_assignment_and_no_recording():
    value = intent(status="preparing")
    requests = []

    async def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        claims = decode(request.headers["authorization"].removeprefix("Bearer "))
        assert claims["video"]["room"] == value.room_name
        if request.url.path.endswith("CreateRoom"):
            assert body == {
                "name": value.room_name,
                "empty_timeout": 120,
                "departure_timeout": 1,
                "max_participants": 2,
            }
            assert claims["video"] == {"room": value.room_name, "roomCreate": True}
            return reply(request, {"name": value.room_name, "active_recording": False})
        assert body["agent_name"] == "navox-vox" and body["restart_policy"] == "JRP_NEVER"
        assert json.loads(body["metadata"]) == {
            "connection_id": str(value.connection_id),
            "room_name": value.room_name,
            "client_identity": value.client_identity,
            "agent_identity": value.agent_identity,
        }
        assert "token" not in body["metadata"]
        return reply(request, {**body, "id": "AD_synthetic"})

    await LiveKitCloudProvider(CONFIG, transport=httpx.MockTransport(handle)).provision(value)
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["participant-missing", "room-remains", "clock", "redirect", "large"]
)
async def test_uncertain_or_incomplete_provider_cleanup_never_returns_a_verified_receipt(failure):
    value = intent()
    claim = CleanupClaim(
        uuid4(),
        value.connection_id,
        uuid4(),
        value.expires_at,
        value.room_name,
        value.client_identity,
        value.agent_identity,
    )

    async def handle(request):
        if failure == "participant-missing" and request.url.path.endswith("RemoveParticipant"):
            return reply(request, {"code": "not_found"}, status=404)
        if failure == "redirect":
            return reply(request, {}, status=307)
        if failure == "large":
            return reply(request, {"private": "x" * 70000})
        if failure == "clock":
            return reply(
                request,
                {},
                date=format_datetime(datetime.now(UTC) - timedelta(minutes=2), usegmt=True),
            )
        return reply(
            request,
            {"rooms": [{"name": value.room_name}]}
            if failure == "room-remains" and request.url.path.endswith("ListRooms")
            else {},
        )

    with pytest.raises(MediaUnavailable) as raised:
        await LiveKitCloudProvider(CONFIG, transport=httpx.MockTransport(handle)).revoke(claim)
    assert "private" not in str(raised.value)


@pytest.mark.asyncio
async def test_cloud_revocation_requires_two_explicit_cutoffs_then_room_absence():
    value = intent()
    claim = CleanupClaim(
        uuid4(),
        value.connection_id,
        uuid4(),
        value.expires_at,
        value.room_name,
        value.client_identity,
        value.agent_identity,
    )
    removed = []
    methods = []

    async def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        body = json.loads(request.content)
        if method == "RemoveParticipant":
            assert body["room"] == value.room_name
            assert 20 < body["revoke_token_ts"] - datetime.now(UTC).timestamp() < 40
            removed.append(body["identity"])
        return reply(request, {})

    receipt = await LiveKitCloudProvider(CONFIG, transport=httpx.MockTransport(handle)).revoke(
        claim
    )
    assert receipt.connection_id == value.connection_id and receipt.claim_id == claim.claim_id
    assert receipt.client_revocation_verified and receipt.agent_revocation_verified
    assert receipt.room_absence_verified
    assert removed == [value.client_identity, value.agent_identity]
    assert methods == [
        "ListRooms",
        "RemoveParticipant",
        "RemoveParticipant",
        "DeleteRoom",
        "ListRooms",
    ]


class FakeProvider(LiveKitCloudProvider):
    def __init__(self, *, fail=False):
        super().__init__(CONFIG)
        self.intents = []
        self.fail = fail

    async def provision(self, value):
        self.intents.append(value)
        if self.fail:
            raise MediaUnavailable("private provider failure that must never appear in HTTP")


@pytest.mark.asyncio
async def test_http_admission_replays_exact_epoch_renews_binding_and_close_survives_disabled_flags(
    vox_env,
):
    env = vox_env
    provider = FakeProvider()
    env.app.dependency_overrides[media_provider] = lambda: provider
    row, _ = await start(env)
    path = f"{ROOT}/{row['client_call_id']}/media"
    request = {"generation": row["generation"], "request_id": str(uuid4())}
    admitted = await env.client.post(path, json=request)
    assert admitted.status_code == 200, admitted.text
    assert admitted.headers["cache-control"] == "private, no-store"
    binding = admitted.json()
    assert decode(binding["token"])["sub"] == binding["participant_identity"]
    assert len(provider.intents) == 1
    replay = await env.client.post(path, json=request)
    assert replay.status_code == 200 and replay.json()["connection_id"] == binding["connection_id"]
    assert replay.json()["expires_at"] == binding["expires_at"] and len(provider.intents) == 1
    endpoint = path + "/" + binding["connection_id"]
    renewed = await env.client.post(endpoint + "/renew", json={"generation": row["generation"]})
    assert renewed.status_code == 200
    assert renewed.json() == {
        k: v for k, v in binding.items() if k not in {"token", "server_url", "token_expires_at"}
    }
    wrong = await env.client.post(endpoint + "/renew", json={"generation": row["generation"] + 1})
    assert wrong.status_code == 404
    env.app.dependency_overrides.pop(media_provider)
    env.settings.student_vox_enabled = False
    closed = await env.client.post(endpoint + "/close", json={"generation": row["generation"]})
    assert closed.status_code == 204
    async with env.factory() as database:
        connection = await database.get(VoxMediaConnection, UUID(binding["connection_id"]))
        cleanup = await database.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.connection_id == connection.id)
        )
        assert connection.status == "revoked" and cleanup.status == "pending"


@pytest.mark.asyncio
async def test_unqualified_http_admission_has_no_external_or_database_effect(vox_env):
    row, _ = await start(vox_env)
    response = await vox_env.client.post(
        f"{ROOT}/{row['client_call_id']}/media",
        json={"generation": row["generation"], "request_id": str(uuid4())},
    )
    assert response.status_code == 503 and "token" not in response.text
    async with vox_env.factory() as database:
        assert await database.scalar(select(VoxMediaConnection)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,extra,status",
    [
        ({"Origin": "https://unrelated.example"}, {}, 403),
        ({"Authorization": "Bearer untrusted"}, {}, 403),
        ({"X-NavoX-Account": "another-account"}, {}, 409),
        ({}, {"room_name": "client-selected-room"}, 422),
        ({}, {"token": "client-selected-token"}, 422),
        ({}, {"generation": True}, 422),
    ],
)
async def test_admission_rejects_bearer_csrf_account_swap_and_client_selected_authority(
    vox_env,
    headers,
    extra,
    status,
):
    provider = FakeProvider()
    vox_env.app.dependency_overrides[media_provider] = lambda: provider
    row, _ = await start(vox_env)
    response = await vox_env.client.post(
        f"{ROOT}/{row['client_call_id']}/media",
        headers=headers,
        json={"generation": row["generation"], "request_id": str(uuid4()), **extra},
    )
    assert response.status_code == status
    assert response.headers["cache-control"] == "private, no-store"
    assert provider.intents == []
    async with vox_env.factory() as database:
        assert await database.scalar(select(VoxMediaConnection)) is None


@pytest.mark.asyncio
async def test_failed_provider_call_still_retires_uncertain_room_and_sanitizes_http(vox_env):
    provider = FakeProvider(fail=True)
    vox_env.app.dependency_overrides[media_provider] = lambda: provider
    row, _ = await start(vox_env)
    response = await vox_env.client.post(
        f"{ROOT}/{row['client_call_id']}/media",
        json={"generation": row["generation"], "request_id": str(uuid4())},
    )
    assert response.status_code == 503 and "private provider" not in response.text
    async with vox_env.factory() as database:
        connection = await database.scalar(select(VoxMediaConnection))
        cleanup = await database.scalar(select(VoxMediaCleanup))
        assert connection.status == "revoked" and cleanup.status == "pending"


@pytest.mark.asyncio
async def test_cancelled_http_owner_cannot_leave_late_provider_success_confirmed(vox_env):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowProvider(FakeProvider):
        async def provision(self, value):
            entered.set()
            await release.wait()

    row, _ = await start(vox_env)
    async with vox_env.factory() as database:
        value = await media.begin_connection(
            database,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
            UUID(row["client_call_id"]),
            generation=row["generation"],
            request_id=uuid4(),
        )
    task = asyncio.create_task(
        admission.provision(
            vox_env.factory,
            SlowProvider(),
            value,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    pending = tuple(admission._PROVISIONS)
    assert pending
    await asyncio.gather(*pending, return_exceptions=True)
    async with vox_env.factory() as database:
        connection = await database.get(VoxMediaConnection, value.connection_id)
        cleanup = await database.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.connection_id == value.connection_id)
        )
        assert connection.status == "revoked" and cleanup.status == "pending"


@pytest.mark.asyncio
async def test_concurrent_exact_retries_share_one_provider_attempt(vox_env):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowProvider(FakeProvider):
        async def provision(self, value):
            self.intents.append(value)
            entered.set()
            await release.wait()

    provider = SlowProvider()
    row, _ = await start(vox_env)
    async with vox_env.factory() as database:
        value = await media.begin_connection(
            database,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
            UUID(row["client_call_id"]),
            generation=row["generation"],
            request_id=uuid4(),
        )
    first = asyncio.create_task(
        admission.provision(
            vox_env.factory,
            provider,
            value,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        admission.provision(
            vox_env.factory,
            provider,
            value,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
        )
    )
    # A barrier scheduled behind both callers observes their shared in-flight task.
    barrier = asyncio.Event()
    asyncio.get_running_loop().call_soon(barrier.set)
    await barrier.wait()
    release.set()
    confirmed = await asyncio.gather(first, second)
    assert len(provider.intents) == 1
    assert all(
        item.connection_id == value.connection_id and item.status == "confirmed"
        for item in confirmed
    )


def verified_receipt(claim):
    return CleanupReceipt(
        claim.connection_id,
        claim.claim_id,
        claim.room_name,
        claim.client_identity,
        claim.agent_identity,
        True,
        True,
        True,
    )


@pytest.mark.asyncio
async def test_unknown_remote_outcome_cannot_be_declared_done_from_current_room_absence(vox_env):
    provider = FakeProvider(fail=True)
    vox_env.app.dependency_overrides[media_provider] = lambda: provider
    row, _ = await start(vox_env)
    response = await vox_env.client.post(
        f"{ROOT}/{row['client_call_id']}/media",
        json={"generation": row["generation"], "request_id": str(uuid4())},
    )
    assert response.status_code == 503
    async with vox_env.factory() as database:
        attempt = await database.scalar(select(VoxMediaProvisionAttempt))
        assert attempt.status == "uncertain" and attempt.finished_at is not None
        claim = await claim_cleanup(database)
        assert claim is not None
    async with vox_env.factory() as database:
        # Even a genuine absence check and both revocations cannot prove that an
        # earlier HTTP timeout won't materialize a later CreateRoom/CreateDispatch.
        assert await finish_cleanup(database, claim, verified_receipt(claim)) is False
        cleanup = await database.get(VoxMediaCleanup, claim.id)
        assert cleanup.status == "pending" and cleanup.completed_at is None


@pytest.mark.asyncio
async def test_crashed_inflight_attempt_survives_account_erasure_and_blocks_false_cleanup(vox_env):
    row, _ = await start(vox_env)
    async with vox_env.factory() as database:
        value = await media.begin_connection(
            database,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
            UUID(row["client_call_id"]),
            generation=row["generation"],
            request_id=uuid4(),
        )
        attempt_id = await attempts.begin_attempt(database, value)
        assert attempt_id is not None
    async with vox_env.factory() as database:
        await database.execute(delete(User).where(User.id == vox_env.user_id))
        await database.commit()
    async with vox_env.factory() as database:
        assert await database.get(VoxMediaConnection, value.connection_id) is None
        attempt = await database.get(VoxMediaProvisionAttempt, attempt_id)
        assert attempt.status == "in-flight" and attempt.finished_at is None
        assert not VoxMediaProvisionAttempt.__table__.foreign_keys
        claim = await claim_cleanup(database)
        assert claim is not None
    async with vox_env.factory() as database:
        assert await finish_cleanup(database, claim, verified_receipt(claim)) is False


@pytest.mark.asyncio
async def test_replica_retry_cannot_start_second_rpc_or_retire_original_holder(vox_env):
    provider = FakeProvider()
    row, _ = await start(vox_env)
    async with vox_env.factory() as database:
        value = await media.begin_connection(
            database,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
            UUID(row["client_call_id"]),
            generation=row["generation"],
            request_id=uuid4(),
        )
        attempt_id = await attempts.begin_attempt(database, value)
    # No in-process _ACTIVE entry represents this other replica's persisted RPC.
    with pytest.raises(service.VoxError) as raised:
        await admission.provision(
            vox_env.factory,
            provider,
            value,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
        )
    assert raised.value.status_code == 409 and provider.intents == []
    async with vox_env.factory() as database:
        cleanup = await database.scalar(select(VoxMediaCleanup))
        assert cleanup.status == "armed"
        assert (await database.get(VoxMediaConnection, value.connection_id)).status == "preparing"
        with pytest.raises(service.VoxError):
            await attempts.finish_attempt(database, value.connection_id, uuid4(), acknowledged=True)
        await attempts.finish_attempt(database, value.connection_id, attempt_id, acknowledged=True)
    confirmed = await admission.provision(
        vox_env.factory,
        provider,
        value,
        vox_env.user_id,
        vox_env.workspace_id,
        vox_env.token_hash,
    )
    assert confirmed.status == "confirmed" and provider.intents == []


@pytest.mark.asyncio
async def test_no_new_provider_io_can_begin_after_cleanup_was_already_verified(vox_env):
    row, _ = await start(vox_env)
    async with vox_env.factory() as database:
        value = await media.begin_connection(
            database,
            vox_env.user_id,
            vox_env.workspace_id,
            vox_env.token_hash,
            UUID(row["client_call_id"]),
            generation=row["generation"],
            request_id=uuid4(),
        )
        await media.revoke_connection(
            database, vox_env.user_id, vox_env.workspace_id, vox_env.token_hash, value.connection_id
        )
        claim = await claim_cleanup(database)
    async with vox_env.factory() as database:
        assert await finish_cleanup(database, claim, verified_receipt(claim)) is True
    async with vox_env.factory() as database:
        with pytest.raises(service.VoxError) as raised:
            await attempts.begin_attempt(database, value)
        assert raised.value.status_code == 410
        assert await database.scalar(select(VoxMediaProvisionAttempt)) is None
