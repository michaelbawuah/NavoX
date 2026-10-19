"""Board storage is additive, encrypted and cannot be downgraded over saved data."""

import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from navox.db.board import SavedBoard, SavedBoardCommand, SavedBoardState


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0047_saved_boards.py"
    spec = importlib.util.spec_from_file_location("navox_saved_board_migration", path)
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


def test_board_upgrade_is_additive_and_matches_owned_encrypted_metadata():
    assert migration().revision == "0047_saved_boards"
    assert migration().down_revision == "0046_student_goals"
    sql = render("upgrade")
    for model in (SavedBoardState, SavedBoard, SavedBoardCommand):
        table = model.__table__
        assert f"CREATE TABLE {table.name}" in sql
        for column in table.columns:
            assert f"{column.name} " in sql
        for index in table.indexes:
            assert f"CREATE INDEX {index.name}" in sql
        for constraint in table.constraints:
            if isinstance(constraint, (UniqueConstraint, ForeignKeyConstraint)) and constraint.name:
                assert f"CONSTRAINT {constraint.name}" in sql
        assert table.columns.user_id.foreign_keys and table.columns.workspace_id.foreign_keys
    assert "FOREIGN KEY(course_id) REFERENCES student_courses (id) ON DELETE CASCADE" in sql
    assert "encrypted_payload TEXT NOT NULL" in sql
    assert "ALTER TABLE" not in sql and "DROP" not in sql and "INSERT INTO" not in sql
    assert all(
        column.name not in {"title", "answers", "marked", "strokes", "lesson_id", "step_index"}
        for model in (SavedBoard, SavedBoardCommand)
        for column in model.__table__.columns
    )
    # Erasure of content or a course must retain opaque retry fences.
    assert not SavedBoardCommand.__table__.columns.board_id.foreign_keys
    assert not SavedBoardCommand.__table__.columns.course_id.foreign_keys


def test_board_downgrade_guards_even_empty_payload_state_and_retry_fences_before_drop():
    sql = render("downgrade")
    for model in (SavedBoardState, SavedBoard, SavedBoardCommand):
        assert f"EXISTS (SELECT 1 FROM {model.__tablename__})" in sql
    assert sql.index("saved private Board data prevents this downgrade") < sql.index("DROP TABLE")
    assert "DELETE FROM" not in sql and "TRUNCATE" not in sql
