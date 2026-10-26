"""Encrypted history, explicit disclosure and erasure contracts (synthetic text)."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from test_vox_calls import vox_env as shared_vox_fixture

from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.communications import AssistantSession
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryPrivacy, MemoryThread
from navox.db.memory import OwnerMemory
from navox.db.session import get_database_session
from navox.memory import history
from navox.memory.service import MemoryError, lock_owner

vox_env = shared_vox_fixture


@pytest_asyncio.fixture
async def history_env(ai_database):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
    )

    async def database_override():
        async with ai_database() as database:
            yield database

    app = create_app()
    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "history-owner@example.com",
                "password": "twelve-character-password",
                "display_name": "History owner",
            },
        )
        assert response.status_code == 201
        account = response.json()
        yield SimpleNamespace(
            client=client,
            factory=ai_database,
            settings=settings,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
        )


async def enable(env, days=30, off_record=False):
    current = (await env.client.get("/api/v1/memory/privacy")).json()
    response = await env.client.put(
        "/api/v1/memory/privacy",
        json={
            "history_enabled": True,
            "retention_days": days,
            "off_the_record": off_record,
            "generation": current["generation"],
            "disclosure_accepted": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["generation"]


async def archive(
    env,
    generation,
    *,
    source_id=None,
    sequence=1,
    when=None,
    question="My synthetic project review is Friday.",
    answer="We can plan around Friday.",
):
    source_id = source_id or uuid4()
    async with env.factory() as database:
        await lock_owner(database, env.user_id)
        saved = await history.append_completed_turn(
            database,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            source_id=source_id,
            source_kind="vox",
            expected_generation=generation,
            sequence=sequence,
            question=question,
            answer=answer,
            now=when,
        )
        await database.commit()
    return saved, source_id


@pytest.mark.asyncio
async def test_skipping_disclosure_never_persists_text_or_settings(history_env):
    env = history_env
    assert (await env.client.get("/api/v1/memory/privacy")).json() == {
        "history_enabled": False,
        "retention_days": None,
        "off_the_record": False,
        "generation": 0,
        "disclosure_version": None,
    }
    assert not (await archive(env, None))[0]
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(MemoryPrivacy)) == 0
        assert await database.scalar(select(func.count()).select_from(MemoryConversation)) == 0
    response = await env.client.put(
        "/api/v1/memory/privacy",
        json={
            "history_enabled": True,
            "generation": 0,
            "retention_days": 30,
        },
    )
    assert response.status_code == 422
    response = await env.client.put(
        "/api/v1/memory/privacy",
        json={
            "history_enabled": True,
            "generation": 0,
            "disclosure_accepted": True,
        },
    )
    assert response.status_code == 422  # Never must be selected explicitly too.


@pytest.mark.parametrize("days", [30, 90, None])
@pytest.mark.asyncio
async def test_each_retention_choice_encrypts_and_reads_completed_turn(history_env, days):
    env = history_env
    generation = await enable(env, days)
    saved, source_id = await archive(env, generation)
    assert saved
    assert not (await archive(env, generation, source_id=source_id))[0]  # retry is idempotent
    rows = (await env.client.get("/api/v1/memory/history?query=Friday")).json()
    assert len(rows) == 1 and rows[0]["message_count"] == 2
    detail = (await env.client.get(f"/api/v1/memory/history/{rows[0]['id']}")).json()
    assert [turn["role"] for turn in detail["turns"]] == ["user", "assistant"]
    assert "Friday" in detail["summary"]
    async with env.factory() as database:
        row = await database.get(MemoryConversation, UUID(rows[0]["id"]))
        assert "Friday" not in row.encrypted_transcript
        assert "Friday" not in row.encrypted_summary
        assert (row.expires_at is None) == (days is None)
        with pytest.raises(MemoryError):
            history.decrypt(
                env.settings,
                uuid4(),
                env.workspace_id,
                row.id,
                "transcript",
                row.encrypted_transcript,
            )
        with pytest.raises(MemoryError):
            history.decrypt(
                env.settings,
                env.user_id,
                env.workspace_id,
                row.id,
                "fact",
                row.encrypted_transcript,
            )


@pytest.mark.asyncio
async def test_off_record_and_stale_provider_completion_never_archive(history_env):
    env = history_env
    generation = await enable(env)
    assert (await archive(env, generation))[0]
    await enable(env, off_record=True)
    assert not (await archive(env, generation))[0]
    async with env.factory() as database:
        assert await history.recording_generation(database, env.user_id, env.workspace_id) is None
    response = await env.client.post(
        "/api/v1/memory/facts", json={"label": "Private", "content": "Keep private"}
    )
    assert response.status_code == 409
    assert len((await env.client.get("/api/v1/memory/history")).json()) == 1


@pytest.mark.asyncio
async def test_source_forget_erases_derivatives_and_fences_replay(history_env):
    env = history_env
    generation = await enable(env)
    _, source_id = await archive(env, generation)
    row = (await env.client.get("/api/v1/memory/history")).json()[0]
    response = await env.client.post(
        "/api/v1/memory/facts",
        json={
            "label": "Review day",
            "content": "Project review is Friday.",
            "source_conversation_id": row["id"],
        },
    )
    assert response.status_code == 201
    assert response.json()["source_kind"] == "vox"
    assert (await env.client.delete(f"/api/v1/memory/history/{row['id']}")).status_code == 204
    assert (await env.client.get("/api/v1/memory/facts")).json() == []
    assert (await env.client.get(f"/api/v1/memory/history/{row['id']}")).status_code == 404
    fresh = (await env.client.get("/api/v1/memory/privacy")).json()["generation"]
    assert fresh > generation
    assert not (await archive(env, generation, source_id=source_id, sequence=2))[0]
    assert not (await archive(env, fresh, source_id=source_id, sequence=2))[0]
    async with env.factory() as database:
        tombstone = await database.get(MemoryConversation, UUID(row["id"]))
        assert tombstone.encrypted_transcript is None and tombstone.encrypted_summary is None


@pytest.mark.asyncio
async def test_autoexpiry_deletes_transcript_summary_but_preserves_visible_fact(history_env):
    env = history_env
    generation = await enable(env)
    _, _ = await archive(env, generation)
    row = (await env.client.get("/api/v1/memory/history")).json()[0]
    response = await env.client.post(
        "/api/v1/memory/facts",
        json={
            "label": "Review day",
            "content": "Project review is Friday.",
            "source_conversation_id": row["id"],
        },
    )
    assert response.status_code == 201
    async with env.factory() as database:
        stored = await database.get(MemoryConversation, UUID(row["id"]))
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    assert (await env.client.get("/api/v1/memory/history")).json() == []
    assert len((await env.client.get("/api/v1/memory/facts")).json()) == 1
    async with env.factory() as database:
        stored = await database.get(MemoryConversation, UUID(row["id"]))
        assert stored.encrypted_transcript is None and stored.encrypted_summary is None


@pytest.mark.asyncio
async def test_sensitive_fact_requires_separate_agreement_and_remains_encrypted(history_env):
    env = history_env
    payload = {"label": "Health", "content": "My synthetic therapy appointment is Thursday."}
    response = await env.client.post("/api/v1/memory/facts", json=payload)
    assert response.status_code == 422
    response = await env.client.post(
        "/api/v1/memory/facts", json={**payload, "sensitive_agreement": True}
    )
    assert response.status_code == 201 and response.json()["sensitive"]
    identifier = response.json()["id"]
    async with env.factory() as database:
        row = await database.get(MemoryFact, UUID(identifier))
        assert "therapy" not in row.encrypted_content
    response = await env.client.patch(
        f"/api/v1/memory/facts/{identifier}",
        json={
            "content": "My synthetic therapy appointment is Friday.",
            "sensitive_agreement": True,
        },
    )
    assert response.status_code == 200 and "Friday" in response.json()["content"]


@pytest.mark.asyncio
async def test_delete_all_erases_legacy_and_native_runtime_boundary(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation)
    assert (
        await env.client.post(
            "/api/v1/memory/facts", json={"label": "Review", "content": "Friday review"}
        )
    ).status_code == 201
    assert (
        await env.client.post("/api/v1/memory", json={"kind": "tone", "content": "friendly"})
    ).status_code == 201
    async with env.factory() as database:
        database.add(
            AssistantSession(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                mode="PERSONAL",
                status="active",
            )
        )
        await database.commit()
    response = await env.client.request(
        "DELETE",
        "/api/v1/memory/all",
        json={
            "generation": generation,
            "confirmation": "DELETE EVERYTHING",
        },
    )
    assert response.status_code == 204, response.text
    async with env.factory() as database:
        for model in (MemoryFact, MemoryThread, OwnerMemory, AssistantSession):
            assert await database.scalar(select(func.count()).select_from(model)) == 0
    assert not (await env.client.get("/api/v1/memory/privacy")).json()["history_enabled"]
    assert not (await archive(env, generation))[0]


@pytest.mark.asyncio
async def test_history_and_fact_ids_cannot_cross_accounts(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation)
    row = (await env.client.get("/api/v1/memory/history")).json()[0]
    await env.client.post("/api/v1/auth/logout")
    response = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "history-other@example.com",
            "password": "twelve-character-password",
            "display_name": "Other history owner",
        },
    )
    assert response.status_code == 201
    assert (await env.client.get(f"/api/v1/memory/history/{row['id']}")).status_code == 404
    assert (await env.client.delete(f"/api/v1/memory/history/{row['id']}")).status_code == 404
    assert (
        await env.client.post(
            "/api/v1/memory/facts",
            json={
                "label": "Borrowed",
                "content": "Not mine",
                "source_conversation_id": row["id"],
            },
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_stale_privacy_and_delete_all_requests_are_rejected(history_env):
    env = history_env
    generation = await enable(env)
    assert generation == 1
    response = await env.client.put(
        "/api/v1/memory/privacy",
        json={
            "generation": 0,
            "history_enabled": False,
            "off_the_record": True,
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_disabling_history_preserves_existing_expiry_and_timed_worker_erases(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation)
    async with env.factory() as database:
        row = await database.scalar(select(MemoryConversation))
        original_expiry, identifier = row.expires_at, row.id
    response = await env.client.put(
        "/api/v1/memory/privacy",
        json={
            "generation": generation,
            "history_enabled": False,
            "retention_days": None,
            "off_the_record": True,
        },
    )
    assert response.status_code == 200
    async with env.factory() as database:
        row = await database.get(MemoryConversation, identifier)
        assert row.expires_at == original_expiry
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    assert await history.sweep_expired_history(env.factory) == 1
    assert await history.sweep_expired_history(env.factory) == 0
    async with env.factory() as database:
        row = await database.get(MemoryConversation, identifier)
        assert row.encrypted_transcript is None and row.encrypted_summary is None
    response = await env.client.request(
        "DELETE",
        "/api/v1/memory/all",
        json={
            "generation": 0,
            "confirmation": "DELETE EVERYTHING",
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_configured_vox_off_record_checkpoint_contains_no_conversation_text(vox_env):
    from test_vox_context_store import setup

    from navox.db.vox_context import VoxCallCheckpoint
    from navox.vox.context_store import DatabaseContextStore, decode_checkpoint
    from navox.vox.session_context import ContextSnapshot
    from navox.vox.worker import MemoryTurn

    env = vox_env
    intent, _, settings, key = await setup(env)
    async with env.factory() as database:
        database.add(
            MemoryPrivacy(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                history_enabled=True,
                off_the_record=True,
                retention_days=30,
                disclosure_version=1,
                generation=1,
                updated_at=datetime.now(UTC),
            )
        )
        await database.commit()

    @asynccontextmanager
    async def session():
        async with env.factory() as database:
            yield (
                database,
                SimpleNamespace(user_id=env.user_id, workspace_id=env.workspace_id),
                intent,
            )
            await database.commit()

    check = SimpleNamespace(context_session=session)
    store = DatabaseContextStore(check, settings, configuration_id="a" * 64)
    assert await store.load(key) == ContextSnapshot()
    turns = (MemoryTurn("Synthetic private question", "Synthetic private answer"),)
    assert await store.compare_and_set(key, 0, turns)
    assert await store.load(key) == ContextSnapshot(1, turns)
    async with env.factory() as database:
        row = await database.get(VoxCallCheckpoint, key.call_id)
        saved = decode_checkpoint(
            store._cipher,
            key,
            row.auth_session_id,
            row.revision,
            row.encrypted_context,
            configuration_id="a" * 64,
        )
        assert saved == ContextSnapshot(1)  # database contains no private question/answer
        assert await database.scalar(select(func.count()).select_from(MemoryConversation)) == 0
    replacement = DatabaseContextStore(check, settings, configuration_id="a" * 64)
    assert await replacement.load(key) == ContextSnapshot(1)


@pytest.mark.asyncio
async def test_configured_vox_archives_only_completed_dialogue_not_compaction(vox_env):
    from test_vox_context_store import setup

    from navox.vox.context_store import DatabaseContextStore
    from navox.vox.session_context import SUMMARY_LABEL
    from navox.vox.worker import MemoryTurn

    env = vox_env
    intent, _, settings, key = await setup(env)
    async with env.factory() as database:
        database.add(
            MemoryPrivacy(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                history_enabled=True,
                off_the_record=False,
                retention_days=30,
                disclosure_version=1,
                generation=1,
                updated_at=datetime.now(UTC),
            )
        )
        await database.commit()

    @asynccontextmanager
    async def session():
        async with env.factory() as database:
            yield (
                database,
                SimpleNamespace(user_id=env.user_id, workspace_id=env.workspace_id),
                intent,
            )
            await database.commit()

    store = DatabaseContextStore(
        SimpleNamespace(context_session=session), settings, configuration_id="a" * 64
    )
    await store.load(key)
    turns = (MemoryTurn("Synthetic retained question", "Synthetic retained answer"),)
    assert await store.compare_and_set(key, 0, turns)
    compacted = (MemoryTurn(SUMMARY_LABEL, "A lossy synthetic summary.", "summary"),)
    assert await store.compare_and_set(key, 1, compacted)
    async with env.factory() as database:
        rows = list(await database.scalars(select(MemoryConversation)))
        assert len(rows) == 1 and rows[0].message_count == 2
        value = history.history_view(rows[0], settings, detail=True)
        assert value["turns"][0]["content"] == "Synthetic retained question"
