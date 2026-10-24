"""HTTP/DB checks for the active-call panel; no provider or microphone."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_vox_calls import ROOT
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_commands import ready

from navox.db.vox import VoxCall
from navox.db.vox_context import VoxCallCheckpoint
from navox.vox.commands import take_wake_commands
from navox.vox.context_store import checkpoint_cipher, encode_checkpoint
from navox.vox.session_context import CallContextKey, ContextSnapshot
from navox.vox.worker import MemoryTurn

vox_env = shared_vox_fixture


@pytest.mark.asyncio
async def test_typed_queue_uses_same_call_and_accepts_8000_without_lifting_speech_limit(vox_env):
    env = vox_env
    row, _, wake, check = await ready(env)
    path = f"{ROOT}/{row['client_call_id']}/messages"
    body = {"request_id": str(uuid4()), "text": "x" * 8000}
    response = await env.client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert "xxx" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert (await env.client.post(path, json=body)).status_code == 200
    received = await take_wake_commands(check, env.settings)
    assert len(received) == 1 and received[0].text == body["text"]
    assert await take_wake_commands(check, env.settings) == []
    assert (await env.client.post(path, json={**body, "text": "changed"})).status_code == 409
    over = await env.client.post(path, json={**body, "text": "x" * 8001})
    assert over.status_code == 422 and "xxx" not in over.text
    assert (
        await env.client.post(wake, json={**body, "generation": row["generation"]})
    ).status_code == 422


async def seed(env, monkeypatch):
    row, _, _, _ = await ready(env)
    monkeypatch.setattr(
        "navox.vox.panel.load_inference_config",
        lambda settings: SimpleNamespace(model_dump_json=lambda: "{}"),
    )
    async with env.factory() as db:
        call = await db.get(VoxCall, UUID(row["id"]))
        key = CallContextKey(env.user_id, env.workspace_id, call.id, call.generation)
        snapshot = ContextSnapshot(1, (MemoryTurn("Private question", "Private answer"),))
        db.add(
            VoxCallCheckpoint(
                call_id=call.id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                auth_session_id=call.auth_session_id,
                generation=call.generation,
                revision=1,
                encrypted_context=encode_checkpoint(
                    checkpoint_cipher(env.settings),
                    key,
                    call.auth_session_id,
                    snapshot,
                    configuration_id=hashlib.sha256(b"{}").hexdigest(),
                ),
                created_at=call.created_at,
                expires_at=call.expires_at,
            )
        )
        await db.commit()
    return row


@pytest.mark.asyncio
async def test_transcript_is_private_incremental_and_erased_after_hangup(vox_env, monkeypatch):
    env = vox_env
    row = await seed(env, monkeypatch)
    path = f"{ROOT}/{row['client_call_id']}/transcript"
    response = await env.client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["turns"] == [
        {"question": "Private question", "answer": "Private answer", "kind": "dialogue"}
    ]
    assert (await env.client.get(path + "?after=1")).json()["turns"] is None
    env.client.headers["X-NavoX-Account"] = f"{env.workspace_id}:{uuid4()}"
    assert (await env.client.get(path)).status_code == 409
    env.client.headers["X-NavoX-Account"] = f"{env.workspace_id}:{env.user_id}"
    assert (
        await env.client.get(path.replace(row["client_call_id"], str(uuid4())))
    ).status_code == 404
    ended = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/end", json={"generation": row["generation"]}
    )
    assert ended.status_code == 204
    assert (await env.client.get(path)).status_code == 410


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["generation", "expiry", "ciphertext"])
async def test_transcript_rejects_wrong_generation_expired_or_corrupt_checkpoint(
    vox_env, monkeypatch, fault
):
    env = vox_env
    row = await seed(env, monkeypatch)
    async with env.factory() as db:
        checkpoint = await db.get(VoxCallCheckpoint, UUID(row["id"]))
        if fault == "generation":
            # A stale generation must not pass the checkpoint binding.
            call = await db.get(VoxCall, checkpoint.call_id)
            checkpoint.auth_session_id = call.auth_session_id
            checkpoint.generation += 1
        elif fault == "expiry":
            checkpoint.created_at = datetime.now(UTC) - timedelta(minutes=10)
            checkpoint.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            checkpoint.encrypted_context = "corrupt"
        await db.commit()
    response = await env.client.get(f"{ROOT}/{row['client_call_id']}/transcript")
    assert response.status_code in (410, 503)
    assert "Private question" not in response.text and "corrupt" not in response.text


@pytest.mark.asyncio
async def test_same_student_new_signin_cannot_read_or_write_another_session_call(
    vox_env, monkeypatch
):
    env = vox_env
    row = await seed(env, monkeypatch)
    login = await env.client.post(
        "/api/v1/auth/login", json={key: env.credentials[key] for key in ("email", "password")}
    )
    assert login.status_code == 200
    assert env.client.cookies.get(env.settings.session_cookie_name) != env.token
    root = f"{ROOT}/{row['client_call_id']}"
    response = await env.client.get(root + "/transcript")
    assert response.status_code == 410
    assert "Private question" not in response.text and "Private answer" not in response.text
    response = await env.client.post(
        root + "/messages", json={"request_id": str(uuid4()), "text": "Wrong session question"}
    )
    assert response.status_code == 410
