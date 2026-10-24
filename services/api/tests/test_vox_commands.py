"""Real HTTP/DB queue tests. No speech provider or raw audio is used."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from test_vox_calls import ROOT
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_media import begin, confirm

from navox.db.vox_commands import VoxWakeCommand
from navox.vox.commands import take_wake_commands
from navox.vox.worker import DatabaseAuthority, WorkerAssignment

vox_env = shared_vox_fixture


async def ready(env):
    env.settings.connector_secret_encryption_key = SecretStr(Fernet.generate_key().decode())
    row, intent = await begin(env)
    intent = await confirm(env, intent)
    path = f"{ROOT}/{row['client_call_id']}/media/{intent.connection_id}/wake"
    check = DatabaseAuthority(
        env.factory,
        WorkerAssignment(
            intent.connection_id, intent.room_name, intent.client_identity, intent.agent_identity
        ),
    )
    return row, intent, path, check


@pytest.mark.asyncio
async def test_real_wake_queue_encrypts_delivers_once_and_fences_changed_retry(vox_env):
    env = vox_env
    row, intent, path, check = await ready(env)
    question = "Which room is my next engineering class in?"
    body = {"generation": row["generation"], "request_id": str(uuid4()), "text": question}
    response = await env.client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert question not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert (await env.client.post(path, json=body)).json() == response.json()
    assert (
        await env.client.post(path, json={**body, "text": "A changed question"})
    ).status_code == 409
    async with env.factory() as database:
        commands = list(await database.scalars(select(VoxWakeCommand)))
        assert len(commands) == 1
        assert question not in commands[0].encrypted_text
    delivered = await take_wake_commands(check, env.settings)
    assert len(delivered) == 1 and delivered[0].text == question
    assert question not in repr(delivered)
    assert await take_wake_commands(check, env.settings) == []
    assert (await env.client.post(path, json=body)).status_code == 200
    assert await take_wake_commands(check, env.settings) == []
    async with env.factory() as database:
        assert (await database.scalar(select(VoxWakeCommand))).encrypted_text is None


@pytest.mark.asyncio
async def test_queue_denies_wrong_scope_stale_epoch_and_missing_origin(vox_env):
    env = vox_env
    row, intent, path, check = await ready(env)
    body = {"generation": row["generation"], "request_id": str(uuid4()), "text": "Hello"}
    assert (
        await env.client.post(path.replace(str(intent.connection_id), str(uuid4())), json=body)
    ).status_code == 404
    assert (
        await env.client.post(path, json={**body, "generation": row["generation"] + 1})
    ).status_code == 410
    saved = env.client.headers.pop("Origin")
    assert (await env.client.post(path, json=body)).status_code == 403
    env.client.headers["Origin"] = saved
    env.client.headers["X-NavoX-Account"] = f"{env.workspace_id}:{uuid4()}"
    assert (await env.client.post(path, json=body)).status_code == 409


@pytest.mark.asyncio
async def test_expired_command_is_erased_without_delivery(vox_env):
    env = vox_env
    row, _, path, check = await ready(env)
    body = {
        "generation": row["generation"],
        "request_id": str(uuid4()),
        "text": "Ephemeral question",
    }
    assert (await env.client.post(path, json=body)).status_code == 200
    async with env.factory() as database:
        item = await database.scalar(select(VoxWakeCommand))
        item.created_at = datetime.now(UTC) - timedelta(seconds=60)
        item.expires_at = datetime.now(UTC) - timedelta(seconds=30)
        await database.commit()
    assert await take_wake_commands(check, env.settings) == []
    async with env.factory() as database:
        assert (await database.scalar(select(VoxWakeCommand))).encrypted_text is None


@pytest.mark.asyncio
async def test_hangup_erases_queued_question_and_invalid_input_never_echoes_it(vox_env):
    env = vox_env
    row, _, path, _ = await ready(env)
    question = "Private unspoken question"
    body = {"generation": row["generation"], "request_id": str(uuid4()), "text": question}
    invalid = await env.client.post(path, json={**body, "extra": question})
    assert invalid.status_code == 422 and question not in invalid.text
    assert (await env.client.post(path, json=body)).status_code == 200
    ended = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/end", json={"generation": row["generation"]}
    )
    assert ended.status_code == 204
    async with env.factory() as database:
        assert await database.scalar(select(VoxWakeCommand)) is None
    assert (await env.client.post(path, json=body)).status_code == 410


@pytest.mark.asyncio
async def test_queue_accepts_full_speech_limit_but_rejects_overlong_and_unconfigured(vox_env):
    env = vox_env
    row, _, path, _ = await ready(env)
    body = {"generation": row["generation"], "request_id": str(uuid4()), "text": "x" * 4000}
    assert (await env.client.post(path, json=body)).status_code == 200
    overlong = await env.client.post(path, json={**body, "text": "x" * 4001})
    assert overlong.status_code == 422 and "xxx" not in overlong.text
    env.settings.connector_secret_encryption_key = None
    env.settings.google_token_encryption_key = None
    assert (
        await env.client.post(path, json={**body, "request_id": str(uuid4())})
    ).status_code == 503


@pytest.mark.asyncio
async def test_one_poll_consumes_one_question_and_retains_other_pending_questions(vox_env):
    env = vox_env
    row, _, path, check = await ready(env)
    request_ids = {str(uuid4()) for _ in range(3)}
    for request_id in request_ids:
        response = await env.client.post(
            path,
            json={"generation": row["generation"], "request_id": request_id, "text": request_id},
        )
        assert response.status_code == 200 and response.json()["delivery"] == "queued"
    observed = []
    for remaining in (2, 1, 0):
        delivered = await take_wake_commands(check, env.settings)
        assert len(delivered) == 1
        observed.append(str(delivered[0].request_id))
        async with env.factory() as database:
            pending = list(
                await database.scalars(
                    select(VoxWakeCommand).where(VoxWakeCommand.encrypted_text.is_not(None))
                )
            )
            assert len(pending) == remaining
    assert set(observed) == request_ids and len(set(observed)) == 3


@pytest.mark.asyncio
async def test_independent_expiry_sweep_erases_crashed_worker_ciphertext_in_bounded_batches(
    vox_env,
):
    from navox.connectors.sync_state import database_now
    from navox.vox.commands import sweep_expired_wake_commands

    env = vox_env
    row, _, path, check = await ready(env)
    bodies = [
        {"generation": row["generation"], "request_id": str(uuid4()), "text": f"Private {index}"}
        for index in range(4)
    ]
    for body in bodies:
        assert (await env.client.post(path, json=body)).status_code == 200
    async with env.factory() as database:
        now = await database_now(database)
        commands = list(await database.scalars(select(VoxWakeCommand).order_by(VoxWakeCommand.id)))
        live_id = commands[-1].id
        for command in commands[:-1]:
            command.created_at = now - timedelta(seconds=60)
            command.expires_at = now - timedelta(seconds=30)
        await database.commit()
    # No voice-worker poll occurs while the independent sweep removes expired bodies.
    async with env.factory() as database:
        assert await sweep_expired_wake_commands(database, limit=2) == 2
    async with env.factory() as database:
        assert await sweep_expired_wake_commands(database, limit=2) == 1
    async with env.factory() as database:
        assert await sweep_expired_wake_commands(database, limit=2) == 0
        commands = list(await database.scalars(select(VoxWakeCommand)))
        assert len(commands) == 4  # Replay fences are retained.
        assert all(command.encrypted_text is None for command in commands if command.id != live_id)
        assert next(command for command in commands if command.id == live_id).encrypted_text
    # Identical expired retries acknowledge the same delivery; never requeue its text.
    for body in bodies:
        assert (await env.client.post(path, json=body)).status_code == 200
    delivered = await take_wake_commands(check, env.settings)
    assert len(delivered) == 1
    assert await take_wake_commands(check, env.settings) == []


@pytest.mark.asyncio
async def test_expired_identical_retry_clears_ciphertext_even_before_global_sweep(vox_env):
    from navox.connectors.sync_state import database_now

    env = vox_env
    row, _, path, _ = await ready(env)
    body = {"generation": row["generation"], "request_id": str(uuid4()), "text": "Private retry"}
    assert (await env.client.post(path, json=body)).status_code == 200
    async with env.factory() as database:
        now = await database_now(database)
        command = await database.scalar(select(VoxWakeCommand))
        command.created_at = now - timedelta(seconds=60)
        command.expires_at = now - timedelta(seconds=1)
        await database.commit()
    assert (await env.client.post(path, json=body)).status_code == 200
    async with env.factory() as database:
        assert (await database.scalar(select(VoxWakeCommand))).encrypted_text is None


@pytest.mark.asyncio
async def test_foreign_cookie_session_cannot_enqueue_to_owned_epoch(vox_env):
    from httpx import ASGITransport, AsyncClient

    env = vox_env
    row, _, path, _ = await ready(env)
    private = "A foreign account must never submit to this epoch"
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as other:
        registered = await other.post(
            "/api/v1/auth/register",
            json={
                "email": "foreign-wake@example.com",
                "password": "twelve-character-password",
                "display_name": "Foreign synthetic account",
            },
        )
        assert registered.status_code == 201, registered.text
        account = registered.json()
        other.headers["X-NavoX-Account"] = f"{account['workspace']['id']}:{account['id']}"
        denied = await other.post(
            path,
            json={"generation": row["generation"], "request_id": str(uuid4()), "text": private},
        )
        assert denied.status_code == 404 and private not in denied.text
    async with env.factory() as database:
        assert await database.scalar(select(VoxWakeCommand)) is None


@pytest.mark.asyncio
async def test_malformed_wake_bodies_never_echo_or_log_question(vox_env, caplog):
    from navox.api.vox_commands import WakeInput

    env = vox_env
    row, _, path, _ = await ready(env)
    private = "PRIVATE_WAKE_BODY_MUST_NEVER_BE_LOGGED"
    request_id = str(uuid4())
    invalid_bodies = [
        f'{{"text":"{private}","text":"again","generation":0,"request_id":"{request_id}"}}',
        f'{{"text":"{private}","generation":true,"request_id":"{request_id}"}}',
        f'{{"text":"{private}","generation":0,"request_id":"not-a-uuid"}}',
        f'{{"text":"{private}","generation":0,"request_id":"{request_id}",',
    ]
    for body in invalid_bodies:
        response = await env.client.post(
            path, content=body, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        assert private not in response.text and private not in caplog.text
        assert response.headers["cache-control"] == "private, no-store"
    payload = WakeInput(generation=row["generation"], request_id=uuid4(), text=private)
    assert private not in repr(payload)
    async with env.factory() as database:
        assert await database.scalar(select(VoxWakeCommand)) is None


@pytest.mark.asyncio
async def test_corrupted_ciphertext_is_consumed_without_plaintext_or_retry_loop(vox_env):
    env = vox_env
    row, _, path, check = await ready(env)
    body = {
        "generation": row["generation"],
        "request_id": str(uuid4()),
        "text": "Private corruption",
    }
    assert (await env.client.post(path, json=body)).status_code == 200
    async with env.factory() as database:
        command = await database.scalar(select(VoxWakeCommand))
        command.encrypted_text = "not-a-valid-authenticated-ciphertext"
        await database.commit()
    assert await take_wake_commands(check, env.settings) == []
    async with env.factory() as database:
        assert (await database.scalar(select(VoxWakeCommand))).encrypted_text is None


@pytest.mark.asyncio
async def test_actual_postgres_parallel_consumers_deliver_each_command_at_most_once(vox_env):
    import asyncio

    if vox_env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL row-lock serialization; SQLite is not evidence")
    env = vox_env
    row, _, path, check = await ready(env)
    identifiers = {str(uuid4()) for _ in range(2)}
    for identifier in identifiers:
        assert (
            await env.client.post(
                path,
                json={
                    "generation": row["generation"],
                    "request_id": identifier,
                    "text": identifier,
                },
            )
        ).status_code == 200
    first, second = await asyncio.gather(
        take_wake_commands(check, env.settings), take_wake_commands(check, env.settings)
    )
    assert len(first) == len(second) == 1
    assert {str(item.request_id) for item in first + second} == identifiers
    assert await take_wake_commands(check, env.settings) == []


@pytest.mark.asyncio
async def test_periodic_reconcile_erases_expired_body_with_live_epoch_and_no_cleanup_due(vox_env):
    from navox.connectors.sync_state import database_now
    from navox.vox.reconcile import reconcile_one

    env = vox_env
    row, _, path, check = await ready(env)
    body = {"generation": row["generation"], "request_id": str(uuid4()), "text": "Unspoken private"}
    assert (await env.client.post(path, json=body)).status_code == 200
    async with env.factory() as database:
        now = await database_now(database)
        command = await database.scalar(select(VoxWakeCommand))
        command.created_at = now - timedelta(seconds=60)
        command.expires_at = now - timedelta(seconds=1)
        await database.commit()

    class NoProviderCall:
        async def revoke(self, claim):
            pytest.fail("No media cleanup is due; this test must not call or qualify a provider")

    assert await reconcile_one(env.factory, NoProviderCall()) is False
    await check()  # The live epoch remains valid; this was an expiry-only cleanup.
    async with env.factory() as database:
        assert (await database.scalar(select(VoxWakeCommand))).encrypted_text is None
