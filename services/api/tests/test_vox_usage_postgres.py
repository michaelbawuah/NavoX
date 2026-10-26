"""Real isolated PostgreSQL quota interleaving and Alembic upgrade/downgrade."""

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_engine_from_config as real_async_engine_from_config
from sqlalchemy.ext.asyncio import create_async_engine
from test_vox_calls import start
from test_vox_calls import vox_env as _shared_vox_env

from navox.core import settings as settings_module
from navox.core.settings import Settings
from navox.vox import usage
from navox.vox.usage_schemas import UsagePolicy

vox_env = _shared_vox_env
pytestmark = pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Requires explicitly configured isolated PostgreSQL",
)


@pytest.mark.asyncio
async def test_concurrent_reservations_serialize_before_paid_io(vox_env):
    env = vox_env
    call, _ = await start(env)
    async with env.factory() as database:
        await usage.update_policy(
            database, UsagePolicy(daily_tokens=100, monthly_tokens=100), expected_revision=0
        )
        await database.commit()
    locked, release, contender_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def first():
        async with env.factory() as database:
            await usage.admit_provider_stream(
                database,
                env.user_id,
                env.workspace_id,
                UUID(call["id"]),
                stream_id=uuid4(),
                provider="synthetic",
                model="fixture",
                token_ceiling=80,
            )
            locked.set()
            await release.wait()
            await database.commit()

    async def second():
        await locked.wait()
        async with env.factory() as database:
            contender_started.set()
            with pytest.raises(usage.UsageError) as failure:
                await usage.admit_provider_stream(
                    database,
                    env.user_id,
                    env.workspace_id,
                    UUID(call["id"]),
                    stream_id=uuid4(),
                    provider="synthetic",
                    model="fixture",
                    token_ceiling=80,
                )
            assert failure.value.status_code == 429

    a, b = asyncio.create_task(first()), asyncio.create_task(second())
    try:
        await asyncio.wait_for(contender_started.wait(), timeout=10)
        await asyncio.sleep(0.1)
        assert not b.done(), "Contender must wait for the owner row lock"
        release.set()
        await asyncio.wait_for(asyncio.gather(a, b), timeout=15)
        async with env.factory() as database:
            result = await usage.budget_status(database, env.user_id, env.workspace_id)
            assert result.daily.reserved_tokens == 80 and result.remaining_tokens == 20
    finally:
        release.set()
        await asyncio.gather(a, b, return_exceptions=True)


@pytest.mark.asyncio
async def test_actual_alembic_0053_0054_upgrade_downgrade_and_history_refusal(monkeypatch):
    dsn = os.environ["NAVOX_CONNECTOR_TEST_DSN"]
    schema = "vox_m1_migration_" + uuid4().hex
    engine = create_async_engine(dsn)
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    settings = Settings(_env_file=None, database_url=dsn, app_environment="test")
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    def scoped_engine(config, *args, **kwargs):
        kwargs["connect_args"] = {"server_settings": {"search_path": schema}}
        return real_async_engine_from_config(config, *args, **kwargs)

    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_engine_from_config", scoped_engine)
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    expected_head = ScriptDirectory.from_config(cfg).get_current_head()
    assert expected_head is not None

    async def migrate(function, revision):
        await asyncio.to_thread(function, cfg, revision)

    scoped = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    try:
        await migrate(command.upgrade, "0052_student_calendar_events")
        async with scoped.connect() as connection:
            baseline = await connection.run_sync(
                lambda c: set(inspect(c).get_table_names(schema=schema))
            )
        await migrate(command.upgrade, "head")
        async with scoped.begin() as connection:
            head = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
            assert head == expected_head
            tables = await connection.run_sync(
                lambda c: set(inspect(c).get_table_names(schema=schema))
            )
            assert {
                "vox_usage_policy",
                "vox_usage_streams",
                "vox_usage_events",
                "vox_usage_deltas",
                "vox_wake_commands",
                "vox_voice_credits",
                "vox_call_checkpoints",
            } <= tables - baseline
            await connection.execute(
                text(
                    "INSERT INTO vox_usage_policy(id,revision,definition,updated_at) "
                    "VALUES(1,1,'{}',CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(RuntimeError, match="must be preserved"):
            await migrate(command.downgrade, "0052_student_calendar_events")
        async with scoped.begin() as connection:
            assert (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one() == head
            assert (
                await connection.execute(text("SELECT count(*) FROM vox_usage_policy"))
            ).scalar_one() == 1
            await connection.execute(text("DELETE FROM vox_usage_policy"))
        await migrate(command.downgrade, "0052_student_calendar_events")
        async with scoped.connect() as connection:
            restored = await connection.run_sync(
                lambda c: set(inspect(c).get_table_names(schema=schema))
            )
            assert restored == baseline
        await migrate(command.upgrade, "head")
    finally:
        await scoped.dispose()
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()
