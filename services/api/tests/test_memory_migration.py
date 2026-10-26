"""Freeze the additive PostgreSQL memory DDL and metadata constraints."""

import importlib.util
import io
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, UniqueConstraint

from navox.db.conversation_memory import MemoryConversation, MemoryPrivacy


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0062_conversation_memory.py"
    spec = importlib.util.spec_from_file_location("memory_migration_0062", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_migration_is_additive_and_matches_frozen_owner_constraints():
    module = migration()
    assert module.revision == "0062_conversation_memory"
    assert module.down_revision == "0061_outlook_reply_tracking"
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={
            "as_sql": True,
            "output_buffer": buffer,
            "literal_binds": True,
        },
    )
    with Operations.context(context):
        module.upgrade()
    sql = " ".join(buffer.getvalue().split())
    for table in ("memory_privacy", "memory_conversations", "memory_facts", "memory_threads"):
        assert f"CREATE TABLE {table}" in sql
    for model in (MemoryPrivacy, MemoryConversation):
        for constraint in model.__table__.constraints:
            if isinstance(constraint, (CheckConstraint, UniqueConstraint)):
                assert f"CONSTRAINT {constraint.name}" in sql
    assert "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE" in sql
    assert "ADD COLUMN memory_generation INTEGER" in sql
    assert "ADD COLUMN off_the_record BOOLEAN DEFAULT false NOT NULL" in sql
    assert "INSERT INTO" not in sql and "DROP TABLE" not in sql


@pytest.mark.parametrize(
    "occupied",
    (
        "memory_privacy",
        "memory_conversations",
        "memory_facts",
        "memory_threads",
        "vox_call_checkpoints",
    ),
)
def test_memory_downgrade_refuses_any_saved_memory_or_checkpoint(monkeypatch, occupied):
    module = migration()
    removed = []

    class Bind:
        def execute(self, statement):
            class Result:
                def first(self):
                    return (1,) if occupied in str(statement) else None

            return Result()

    monkeypatch.setattr(module.op, "get_bind", lambda: Bind())
    monkeypatch.setattr(module.op, "drop_column", lambda *args: removed.append(args))
    monkeypatch.setattr(module.op, "drop_table", lambda *args: removed.append(args))
    with pytest.raises(RuntimeError, match="preserve"):
        module.downgrade()
    assert removed == []
