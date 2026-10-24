"""Checkpoint encryption/ownership proofs; no provider audio or phone qualification."""

import asyncio
import importlib.util
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import inspect, select, text
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_media import confirm
from test_vox_worker import prepared

from navox.connectors.sync_state import utc
from navox.db.models import User
from navox.db.vox_context import VoxCallCheckpoint
from navox.vox import context_store, service
from navox.vox.context_store import DatabaseContextStore, decode_checkpoint, encode_checkpoint
from navox.vox.schemas import VoxLease
from navox.vox.session_context import CallContextKey, ContextSnapshot, VoxCallContext
from navox.vox.worker import DatabaseAuthority, MemoryTurn, WorkerAccessLost
from navox.vox.worker_livekit import exclusive_epoch

vox_env = shared_vox_fixture
pg = pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Requires explicitly configured isolated PostgreSQL",
)
TURNS = (MemoryTurn("When is my synthetic class?", "Your synthetic class is at ten."),)


def test_ciphertext_is_bounded_private_and_domain_owner_session_revision_bound():
    cipher = Fernet(Fernet.generate_key())
    key = CallContextKey(uuid4(), uuid4(), uuid4(), 1)
    session_id = uuid4()
    snapshot = ContextSnapshot(1, TURNS)
    encrypted = encode_checkpoint(cipher, key, session_id, snapshot)
    assert TURNS[0].question not in encrypted
    assert TURNS[0].answer not in encrypted
    assert decode_checkpoint(cipher, key, session_id, 1, encrypted) == snapshot
    for altered in (
        replace(key, user_id=uuid4()),
        replace(key, workspace_id=uuid4()),
        replace(key, call_id=uuid4()),
        replace(key, generation=2),
    ):
        with pytest.raises(WorkerAccessLost):
            decode_checkpoint(cipher, altered, session_id, 1, encrypted)
    for args in (
        (cipher, key, uuid4(), 1, encrypted),
        (cipher, key, session_id, 2, encrypted),
        (Fernet(Fernet.generate_key()), key, session_id, 1, encrypted),
        (cipher, key, session_id, 1, "bad-ciphertext"),
        (cipher, key, session_id, 1, None),
    ):
        with pytest.raises(WorkerAccessLost) as error:
            decode_checkpoint(*args)
        assert TURNS[0].question not in str(error.value)
        assert encrypted not in str(error.value)


async def setup(env):
    intent, base = await prepared(env)
    await confirm(env, intent)
    settings = env.settings.model_copy(
        update={"connector_secret_encryption_key": SecretStr(Fernet.generate_key().decode())}
    )
    key = CallContextKey(env.user_id, env.workspace_id, intent.reservation_id, intent.generation)
    return intent, base, settings, key


@asynccontextmanager
async def store_for(env, base, settings):
    async with exclusive_epoch(env.factory.kw["bind"], base.assignment) as connection:
        check = DatabaseAuthority(env.factory, base.assignment, connection)
        yield DatabaseContextStore(check, settings, configuration_id="a" * 64), connection


@pg
@pytest.mark.asyncio
async def test_replacement_worker_restores_completed_turns_after_ram_close(vox_env):
    env = vox_env
    intent, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (store, _):
        context = await VoxCallContext.load(key, store)
        context.begin_epoch(intent.connection_id)
        await context.commit(intent.connection_id, TURNS[0].question, TURNS[0].answer)
        context.close()
        with pytest.raises(WorkerAccessLost):
            context.history(intent.connection_id)
    async with env.factory() as database:
        row = await database.get(VoxCallCheckpoint, key.call_id)
        assert row.revision == 1 and TURNS[0].question not in row.encrypted_context
    async with store_for(env, base, settings) as (store, _):
        assert await store.load(key) == ContextSnapshot(1, TURNS)
        assert not await store.compare_and_set(key, 0, TURNS)


@pg
@pytest.mark.asyncio
async def test_concurrent_cas_has_one_winner_and_does_not_hold_user_during_provider_io(vox_env):
    env = vox_env
    _, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        result = await asyncio.gather(
            store.compare_and_set(key, 0, TURNS),
            store.compare_and_set(key, 0, (MemoryTurn("Other question", "Other answer"),)),
        )
        assert sorted(result) == [False, True]
        async with env.factory() as observer:
            assert (
                await observer.scalar(
                    select(User.id).where(User.id == env.user_id).with_for_update(nowait=True)
                )
                == env.user_id
            )


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["user", "call", "generation", "workspace"])
async def test_scope_cannot_be_supplied_by_the_caller(vox_env, change):
    env = vox_env
    _, base, settings, key = await setup(env)
    changes = {
        "user": {"user_id": uuid4()},
        "call": {"call_id": uuid4()},
        "generation": {"generation": key.generation + 1},
        "workspace": {"workspace_id": uuid4()},
    }
    async with store_for(env, base, settings) as (store, _):
        with pytest.raises(WorkerAccessLost):
            await store.load(replace(key, **changes[change]))


@pg
@pytest.mark.asyncio
async def test_pooled_authority_cannot_read_or_write_durable_context(vox_env):
    _, base, settings, key = await setup(vox_env)
    store = DatabaseContextStore(base, settings, configuration_id="a" * 64)
    with pytest.raises(WorkerAccessLost, match="exclusive"):
        await store.load(key)


@pg
@pytest.mark.asyncio
async def test_lost_physical_lock_stays_terminal_after_successor_takes_over(vox_env):
    env = vox_env
    _, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (original, connection):
        await original.load(key)
        assert await original.compare_and_set(key, 0, TURNS)
        pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        await connection.commit()
        async with env.factory() as killer:
            assert await killer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            await killer.commit()
        async with store_for(env, base, settings) as (successor, _):
            assert await successor.load(key) == ContextSnapshot(1, TURNS)
            for _ in range(2):
                with pytest.raises(WorkerAccessLost, match="connection ended"):
                    await original.compare_and_set(key, 1, TURNS)
            assert await successor.compare_and_set(key, 1, (*TURNS, *TURNS))


@pg
@pytest.mark.asyncio
async def test_renewal_extends_only_live_checkpoint_and_expiry_is_not_silent_amnesia(vox_env):
    env = vox_env
    intent, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        async with env.factory() as database:
            before = utc((await database.get(VoxCallCheckpoint, key.call_id)).expires_at)
            await service.renew(
                database,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                intent.client_call_id,
                VoxLease(generation=key.generation),
            )
            after = utc(
                (
                    await database.get(VoxCallCheckpoint, key.call_id, populate_existing=True)
                ).expires_at
            )
            assert after >= before
            row = await database.get(VoxCallCheckpoint, key.call_id)
            row.created_at = datetime.now(UTC) - timedelta(minutes=5)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await database.commit()
            await service.renew(
                database,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                intent.client_call_id,
                VoxLease(generation=key.generation),
            )
        with pytest.raises(WorkerAccessLost, match="ended"):
            await store.load(key)
        async with env.factory() as database:
            assert await context_store.sweep_expired_checkpoints(database) == 1
            row = await database.get(VoxCallCheckpoint, key.call_id, populate_existing=True)
            assert row.encrypted_context is None
        with pytest.raises(WorkerAccessLost):
            await store.compare_and_set(key, 0, TURNS)


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["hang-up", "sign-out", "forget", "email-forget", "reset"])
async def test_terminal_and_forget_cleanup_remove_ciphertext_and_reject_stale_worker(
    vox_env, ending
):
    env = vox_env
    intent, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        assert await store.compare_and_set(key, 0, TURNS)
        async with env.factory() as database:
            await database.scalar(select(User.id).where(User.id == env.user_id).with_for_update())
            if ending == "hang-up":
                await service.end(
                    database,
                    env.user_id,
                    env.workspace_id,
                    env.token_hash,
                    intent.client_call_id,
                    VoxLease(generation=key.generation),
                )
            elif ending == "sign-out":
                await service.revoke_session_calls(database, env.session_id, now=datetime.now(UTC))
            elif ending == "forget":
                await service.invalidate_active_context(database, env.user_id, env.workspace_id)
            elif ending == "email-forget":
                from navox.student_email.service import forget_email_data

                await forget_email_data(
                    database, user_id=env.user_id, workspace_id=env.workspace_id
                )
            else:
                await service.forget_vox_data(database, env.user_id, env.workspace_id)
            await database.commit()
            assert await database.get(VoxCallCheckpoint, key.call_id) is None
        with pytest.raises(WorkerAccessLost):
            await store.load(key)
        with pytest.raises(WorkerAccessLost):
            await store.compare_and_set(key, 1, TURNS)


@pg
@pytest.mark.asyncio
async def test_forget_wins_while_checkpoint_writer_waits_on_user_lock(vox_env):
    env = vox_env
    _, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        async with env.factory() as eraser:
            await eraser.scalar(select(User.id).where(User.id == env.user_id).with_for_update())
            writer = asyncio.create_task(store.compare_and_set(key, 0, TURNS))
            # Wait until the actual writer reaches the held row, not a timed sleep.
            async with env.factory() as observer:
                for _ in range(100):
                    blocked = await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' "
                            "AND query LIKE 'SELECT users.id%FOR UPDATE%'"
                        )
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                assert blocked
            await service.invalidate_active_context(eraser, env.user_id, env.workspace_id)
            await eraser.commit()
            with pytest.raises(WorkerAccessLost):
                await writer
        async with env.factory() as database:
            assert await database.get(VoxCallCheckpoint, key.call_id) is None


@pg
@pytest.mark.asyncio
async def test_corrupt_checkpoint_never_resumes_with_empty_or_overwritten_context(vox_env):
    env = vox_env
    _, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        async with env.factory() as database:
            row = await database.get(VoxCallCheckpoint, key.call_id)
            row.encrypted_context = "damaged"
            await database.commit()
        with pytest.raises(WorkerAccessLost, match="unavailable"):
            await store.load(key)
        with pytest.raises(WorkerAccessLost, match="unavailable"):
            await store.compare_and_set(key, 0, TURNS)


@pg
@pytest.mark.asyncio
async def test_checkpoint_migration_catalog_roundtrip_and_populated_downgrade_guard(vox_env):
    env = vox_env
    _, base, settings, key = await setup(env)
    path = Path(__file__).parents[1] / "migrations/versions/0056_vox_call_checkpoints.py"
    spec = importlib.util.spec_from_file_location("checkpoint_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0055_vox_voice_credit"
    async with env.factory.kw["bind"].begin() as connection:

        def rebuild(sync):
            VoxCallCheckpoint.__table__.drop(sync)
            with Operations.context(MigrationContext.configure(sync)):
                module.upgrade()
                catalog = inspect(sync)
                table = VoxCallCheckpoint.__table__
                assert {c["name"] for c in catalog.get_columns(table.name)} == set(
                    table.columns.keys()
                )
                assert {i["name"] for i in catalog.get_indexes(table.name)} == {
                    i.name for i in table.indexes
                }
                assert {fk["referred_table"] for fk in catalog.get_foreign_keys(table.name)} == {
                    "vox_calls",
                    "users",
                    "workspaces",
                    "user_sessions",
                }
                module.downgrade()
                module.upgrade()

        await connection.run_sync(rebuild)
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
    async with env.factory.kw["bind"].begin() as connection:

        def refuse(sync):
            with Operations.context(MigrationContext.configure(sync)):
                with pytest.raises(RuntimeError, match="End active"):
                    module.downgrade()
                assert sync.scalar(select(VoxCallCheckpoint.call_id)) == key.call_id

        await connection.run_sync(refuse)


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("has_checkpoint", [True, False])
async def test_new_room_waits_for_cleanup_and_restores_only_existing_call_context(
    vox_env, has_checkpoint
):
    from navox.db.vox_media import VoxMediaCleanup
    from navox.vox import media
    from navox.vox.worker import WorkerAssignment

    env = vox_env
    intent, base, settings, key = await setup(env)
    if has_checkpoint:
        async with store_for(env, base, settings) as (store, _):
            await store.load(key)
            assert await store.compare_and_set(key, 0, TURNS)
    async with env.factory() as database:
        await media.revoke_connection(
            database, env.user_id, env.workspace_id, env.token_hash, intent.connection_id
        )
        request_id = uuid4()

        async def replacement():
            return await media.begin_connection(
                database,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                intent.client_call_id,
                generation=intent.generation,
                request_id=request_id,
                require_recovery_context=True,
            )

        with pytest.raises(service.VoxError) as error:
            await replacement()
        assert error.value.status_code == 425
        # Synthetic provider cleanup receipt. No real room/provider is used.
        cleanup = await database.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.connection_id == intent.connection_id)
        )
        cleanup.status = "done"
        cleanup.completed_at = datetime.now(UTC)
        cleanup.next_attempt_at = None
        await database.commit()
        if not has_checkpoint:
            with pytest.raises(service.VoxError) as error:
                await replacement()
            assert error.value.status_code == 410
            return
        new_intent = await replacement()
        assert new_intent.connection_id != intent.connection_id
        assert new_intent.reservation_id == key.call_id
        await confirm(env, new_intent)
    new_assignment = WorkerAssignment(
        new_intent.connection_id,
        new_intent.room_name,
        new_intent.client_identity,
        new_intent.agent_identity,
    )
    successor_base = DatabaseAuthority(env.factory, new_assignment)
    async with store_for(env, successor_base, settings) as (successor, _):
        assert await successor.load(key) == ContextSnapshot(1, TURNS)
        with pytest.raises(WorkerAccessLost):
            await base()


@pg
@pytest.mark.asyncio
async def test_checkpoint_commit_rechecks_authority_after_local_work(vox_env, monkeypatch):
    env = vox_env
    _, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        original = store.check._intent_in
        proofs = 0

        async def expire_before_commit(database, *, allow_preparing=False):
            nonlocal proofs
            proofs += 1
            if proofs == 2:
                raise WorkerAccessLost("The voice connection has ended")
            return await original(database, allow_preparing=allow_preparing)

        monkeypatch.setattr(store.check, "_intent_in", expire_before_commit)
        with pytest.raises(WorkerAccessLost):
            await store.compare_and_set(key, 0, TURNS)
        assert proofs == 2
        async with env.factory() as observer:
            checkpoint = await observer.get(VoxCallCheckpoint, key.call_id)
            assert checkpoint.revision == 0
            assert (
                decode_checkpoint(
                    store._cipher,
                    key,
                    env.session_id,
                    0,
                    checkpoint.encrypted_context,
                    configuration_id="a" * 64,
                ).turns
                == ()
            )


@pg
@pytest.mark.asyncio
async def test_replacement_cannot_silently_change_active_call_voice_or_provider_config(vox_env):
    env = vox_env
    _, base, settings, key = await setup(env)
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        assert await store.compare_and_set(key, 0, TURNS)
        changed = DatabaseContextStore(store.check, settings, configuration_id="b" * 64)
        with pytest.raises(WorkerAccessLost, match="unavailable"):
            await changed.load(key)
        with pytest.raises(WorkerAccessLost, match="unavailable"):
            await changed.compare_and_set(key, 1, TURNS)
        assert await store.load(key) == ContextSnapshot(1, TURNS)


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["task", "course"])
async def test_owned_schedule_source_erasure_retires_context_but_rejected_erasure_does_not(
    vox_env, source
):
    from navox.db.student import StudentCourse
    from navox.db.student_planning import StudentTask
    from navox.student import service as student
    from navox.student_planning import service as planning

    env = vox_env
    _, base, settings, key = await setup(env)
    async with env.factory() as database:
        if source == "task":
            row = StudentTask(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                title="Synthetic private task",
                estimated_minutes=30,
                remaining_minutes=30,
            )
        else:
            row = StudentCourse(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                name="Synthetic private course",
                term_start=datetime.now(UTC).date(),
                term_end=(datetime.now(UTC) + timedelta(days=30)).date(),
            )
        database.add(row)
        await database.commit()
        source_id = row.id
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        assert await store.compare_and_set(key, 0, TURNS)

        async def erase(revision):
            async with env.factory() as database:
                if source == "task":
                    await planning.remove_task(
                        database,
                        env.user_id,
                        env.workspace_id,
                        source_id,
                        revision=revision,
                        request_id=uuid4(),
                    )
                else:
                    await student.remove_record(
                        database, env.user_id, env.workspace_id, StudentCourse, source_id, revision
                    )

        with pytest.raises(student.StudentError):
            await erase(999)
        assert await store.load(key) == ContextSnapshot(1, TURNS)
        await erase(1)
        with pytest.raises(WorkerAccessLost):
            await store.load(key)
        async with env.factory() as observer:
            assert await observer.get(VoxCallCheckpoint, key.call_id) is None


@pg
@pytest.mark.asyncio
async def test_compacted_checkpoint_survives_worker_replacement_then_is_purged(vox_env):
    from navox.vox.session_context import SUMMARY_LABEL

    env = vox_env
    intent, base, settings, key = await setup(env)
    original = tuple(
        MemoryTurn(f"Synthetic question {i}", "Synthetic details " * 30) for i in range(8)
    )
    async with store_for(env, base, settings) as (store, _):
        await store.load(key)
        assert await store.compare_and_set(key, 0, original)
        context = await VoxCallContext.load(key, store)
        context.begin_epoch(intent.connection_id)
        assert await context.compact(
            intent.connection_id, original[:-2], "The room was corrected to 42."
        )
        context.close()
    async with store_for(env, base, settings) as (successor, _):
        snapshot = await successor.load(key)
        assert snapshot.turns == (
            MemoryTurn(SUMMARY_LABEL, "The room was corrected to 42.", "summary"),
            *original[-2:],
        )
        async with env.factory() as db:
            await service.end(
                db,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                intent.client_call_id,
                VoxLease(generation=key.generation),
            )
            assert await db.get(VoxCallCheckpoint, key.call_id) is None
        with pytest.raises(WorkerAccessLost):
            await successor.load(key)
