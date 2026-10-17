"""Render PostgreSQL DDL and compare memory constraints with registered ORM metadata."""

import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, UniqueConstraint

from navox.db.memory import OwnerMemory

MIGRATION_FILE = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0038_owner_memory.py"
)


def migration_module():
    spec = importlib.util.spec_from_file_location("navox_migration_0038", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(direction):
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer, "literal_binds": True},
    )
    with Operations.context(context):
        getattr(migration_module(), direction)()
    return " ".join(buffer.getvalue().split())


def test_memory_migration_is_additive_and_has_no_import_or_extraction():
    module = migration_module()
    assert module.revision == "0038_owner_memory"
    assert module.down_revision == "0037_personal_routines"
    sql = render("upgrade")
    assert "CREATE TABLE owner_memories" in sql
    assert "FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE" in sql
    assert "UNIQUE (workspace_id, user_id, preference_key)" in sql
    assert "INSERT INTO" not in sql
    assert "ALTER TABLE" not in sql
    assert "DROP" not in sql
    table = OwnerMemory.__table__
    for constraint in table.constraints:
        if isinstance(constraint, (CheckConstraint, UniqueConstraint)):
            assert f"CONSTRAINT {constraint.name}" in sql
    assert "preference_key IS NOT NULL" in sql
    assert "content VARCHAR(500) NOT NULL" in sql


def test_memory_downgrade_refuses_to_silently_discard_saved_facts():
    sql = render("downgrade")
    assert "saved owner memory prevents this downgrade" in sql
    assert "IF EXISTS (SELECT 1 FROM owner_memories)" in sql
    assert "DROP TABLE owner_memories" in sql
    assert "DELETE FROM" not in sql
