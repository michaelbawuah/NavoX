"""The goal migration adds only its own tables and refuses destructive downgrade."""

import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from navox.db.student_goals import (
    StudentGoal,
    StudentGoalCheckIn,
    StudentGoalCommand,
    StudentGoalState,
)


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0046_student_goals.py"
    spec = importlib.util.spec_from_file_location("navox_student_goals_migration", path)
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


def test_goal_upgrade_is_additive_and_metadata_matches_owned_encrypted_tables():
    assert migration().down_revision == "0045_student_email"
    sql = render("upgrade")
    models = (StudentGoalState, StudentGoal, StudentGoalCheckIn, StudentGoalCommand)
    for model in models:
        table = model.__table__
        assert f"CREATE TABLE {table.name}" in sql
        for column in table.columns:
            assert f"{column.name} " in sql
        for index in table.indexes:
            assert f"CREATE INDEX {index.name}" in sql
        for constraint in table.constraints:
            if isinstance(constraint, (UniqueConstraint, ForeignKeyConstraint)) and constraint.name:
                assert f"CONSTRAINT {constraint.name}" in sql
    assert (
        "FOREIGN KEY(goal_id, user_id, workspace_id) REFERENCES student_goals "
        "(id, user_id, workspace_id) ON DELETE CASCADE" in sql
    )
    assert "ALTER TABLE" not in sql and "DROP" not in sql and "INSERT INTO" not in sql
    assert "encrypted_payload TEXT NOT NULL" in sql
    assert all(
        column.name not in {"title", "note", "description", "count", "completed"}
        for model in models
        for column in model.__table__.columns
    )
    # No goal FK can silently remove the content-free privacy retry fences.
    assert not StudentGoalCommand.__table__.columns.goal_id.foreign_keys


def test_goal_downgrade_guards_all_state_before_dropping_any_table():
    sql = render("downgrade")
    for model in (StudentGoalState, StudentGoal, StudentGoalCheckIn, StudentGoalCommand):
        assert f"EXISTS (SELECT 1 FROM {model.__tablename__})" in sql
    assert sql.index("saved private goal data prevents this downgrade") < sql.index("DROP TABLE")
    assert "DELETE FROM" not in sql and "TRUNCATE" not in sql
