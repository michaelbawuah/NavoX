import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import UniqueConstraint

from navox.db.student_recovery import StudentRecoveryPreview


def migration():
    source = Path(__file__).resolve().parents[1] / "migrations/versions/0044_student_recovery.py"
    spec = importlib.util.spec_from_file_location("navox_student_recovery_migration", source)
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


def test_recovery_migration_is_additive_with_private_nullable_ciphertext_and_fenced_receipts():
    assert migration().down_revision == "0043_student_coursework"
    sql = render("upgrade")
    table = StudentRecoveryPreview.__table__
    assert f"CREATE TABLE {table.name}" in sql
    for column in table.columns:
        assert f"{column.name} " in sql
    for index in table.indexes:
        assert f"CREATE INDEX {index.name}" in sql
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            assert f"CONSTRAINT {constraint.name}" in sql
    assert "REFERENCES student_recovery_previews (id) ON DELETE CASCADE" in sql
    assert "INSERT INTO" not in sql and "ALTER TABLE" not in sql and "DROP" not in sql


def test_recovery_downgrade_refuses_to_discard_any_private_receipt():
    sql = render("downgrade")
    assert "EXISTS (SELECT 1 FROM student_recovery_previews)" in sql
    assert "saved student recovery receipts prevent this downgrade" in sql
    assert "DELETE FROM" not in sql
