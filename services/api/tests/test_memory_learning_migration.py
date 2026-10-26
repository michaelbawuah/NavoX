"""The optional semantic queue never imports source text or drops retained proposals."""

import importlib.util
import io
import os
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from test_conversation_memory import archive, enable
from test_conversation_memory import history_env as shared_history_fixture

history_env = shared_history_fixture


def migration():
    path = (
        Path(__file__).resolve().parents[1] / "migrations/versions/0065_semantic_memory_learning.py"
    )
    spec = importlib.util.spec_from_file_location("semantic_memory_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(direction):
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output, "literal_binds": True},
    )
    with Operations.context(context):
        getattr(migration(), direction)()
    return " ".join(output.getvalue().split())


def test_semantic_upgrade_adds_only_bounded_source_owned_work_without_copying_history():
    module, sql = migration(), render("upgrade")
    assert module.down_revision == "0064_provider_calendar_writes"
    assert "CREATE TABLE memory_learning_jobs" in sql and "encrypted_result TEXT" in sql
    assert "source_conversation_id" in sql and "ON DELETE CASCADE" in sql
    assert "pending_count >= 0 AND pending_count <= 4" in sql
    assert "INSERT INTO" not in sql and "UPDATE memory_conversations" not in sql
    assert "encrypted_input" not in sql and "question" not in sql and "quote" not in sql


def test_semantic_downgrade_locks_before_guard_and_refuses_all_populated_work():
    sql = render("downgrade")
    assert "LOCK TABLE memory_learning_jobs IN ACCESS EXCLUSIVE MODE" in sql
    assert sql.index("IF EXISTS") < sql.index("DROP TABLE")
    assert "Semantic memory work or reviews prevent this downgrade" in sql
    assert "DELETE FROM" not in sql


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Actual PostgreSQL migration and downgrade guard",
)
async def test_postgres_semantic_empty_reupgrade_preserves_history_and_populated_refusal(
    history_env,
):
    env = history_env
    generation = await enable(env)
    await archive(env, generation)

    def run(connection, direction):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration(), direction)()

    async with env.factory() as database:
        before = (
            await database.execute(
                text("SELECT encrypted_transcript, encrypted_summary FROM memory_conversations")
            )
        ).one()
        connection = await database.connection()
        await connection.run_sync(lambda value: run(value, "downgrade"))
        await connection.run_sync(lambda value: run(value, "upgrade"))
        after = (
            await database.execute(
                text("SELECT encrypted_transcript, encrypted_summary FROM memory_conversations")
            )
        ).one()
        assert tuple(before) == tuple(after)
        await database.commit()
    env.settings.memory_semantic_learning_enabled = True
    await archive(env, generation, question="I prefer brief replies. I code in Python.")
    async with env.factory() as database:
        connection = await database.connection()
        async with database.begin_nested():
            with pytest.raises(SQLAlchemyError, match="Semantic memory work or reviews prevent"):
                await connection.run_sync(lambda value: run(value, "downgrade"))
        assert await database.scalar(text("SELECT count(*) FROM memory_learning_jobs")) == 1
