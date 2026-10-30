import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import UniqueConstraint

from navox.db.student_planning import (
    StudentPlanningCommand,
    StudentPlanningPreview,
    StudentPlanningState,
    StudentStudyBlock,
    StudentTask,
)


def migration(filename="0042_student_planning.py"):
    source = Path(__file__).resolve().parents[1] / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location("navox_student_planning_migration", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(direction, filename="0042_student_planning.py"):
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={
            "as_sql": True,
            "output_buffer": output,
            "literal_binds": True,
        },
    )
    with Operations.context(context):
        getattr(migration(filename), direction)()
    return " ".join(output.getvalue().split())


def test_planning_migration_is_additive_and_covers_registered_columns_and_unique_keys():
    assert migration().down_revision == "0041_lms_feeds"
    sql = render("upgrade")
    # The modern model also contains the additive 0059 review snapshot. Keep
    # historical 0042 coverage strict without requiring it to create that column.
    replacement_sql = render("upgrade", "0059_plan_replacement_review.py")
    assert migration("0059_plan_replacement_review.py").down_revision == "0058_noa_settings"
    assert replacement_sql == (
        "ALTER TABLE student_planning_previews ADD COLUMN replaced_blocks JSON;"
    )
    for model in (
        StudentTask,
        StudentPlanningState,
        StudentPlanningPreview,
        StudentStudyBlock,
        StudentPlanningCommand,
    ):
        table = model.__table__
        assert f"CREATE TABLE {table.name}" in sql
        for column in table.columns:
            if (table.name, column.name) == ("student_planning_previews", "replaced_blocks"):
                assert "replaced_blocks " not in sql
                continue
            assert f"{column.name} " in sql
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                assert f"CONSTRAINT {constraint.name}" in sql
        for index in table.indexes:
            assert f"CREATE INDEX {index.name}" in sql
    assert "REFERENCES lms_feed_items (id) ON DELETE CASCADE" in sql
    assert "INSERT INTO" not in sql and "ALTER TABLE" not in sql and "DROP" not in sql


def test_planning_downgrade_refuses_to_drop_any_saved_private_planning_copy():
    sql = render("downgrade")
    for table in (
        "student_tasks",
        "student_study_blocks",
        "student_planning_states",
        "student_planning_previews",
        "student_planning_commands",
    ):
        assert f"EXISTS (SELECT 1 FROM {table})" in sql
    assert "saved student planning data prevents this downgrade" in sql
    assert "DELETE FROM" not in sql
