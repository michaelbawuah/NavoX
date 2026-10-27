"""Provider identity survives revoked consent and blocks unsafe old-reader rollback."""

import importlib.util
import io
import os
from pathlib import Path
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from test_student_calendar_consent import calendar_env as shared_calendar_env
from test_student_calendar_events import event_env as shared_event_env
from test_student_calendar_events import preview

from navox.db.student_calendars import StudentCalendarEvent, StudentCalendarWriteGrant

calendar_env, event_env = shared_calendar_env, shared_event_env


def migration():
    path = (
        Path(__file__).resolve().parents[1] / "migrations/versions/0064_provider_calendar_writes.py"
    )
    spec = importlib.util.spec_from_file_location("provider_calendar_write_migration", path)
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


def test_provider_upgrade_preserves_legacy_google_and_authentication_kinds():
    module = migration()
    assert module.revision == "0064_provider_calendar_writes"
    assert module.down_revision == "0063_apple_calendar_consent"
    sql = render("upgrade")
    for name in ("student_calendar_write_grants", "student_calendar_events"):
        assert (
            f"ALTER TABLE {name} ADD COLUMN provider VARCHAR(16) DEFAULT 'google' NOT NULL" in sql
        )
    assert "ALTER COLUMN oauth_attempt_id DROP NOT NULL" in sql
    assert "provider = 'icloud' AND oauth_attempt_id IS NULL" in sql
    assert "provider IN ('google','microsoft') AND oauth_attempt_id IS NOT NULL" in sql
    assert "DELETE FROM" not in sql and "DROP TABLE" not in sql
    for model in (StudentCalendarWriteGrant, StudentCalendarEvent):
        assert model.__table__.c.provider.server_default.arg == "google"
        for item in model.__table__.constraints:
            if item.name and ("provider" in item.name or "authorization_kind" in item.name):
                assert f"CONSTRAINT {item.name}" in sql


def test_provider_downgrade_locks_and_preserves_non_google_receipts_independently_of_grants():
    sql = render("downgrade")
    assert "LOCK TABLE student_calendar_events, student_calendar_write_grants" in sql
    assert "SELECT 1 FROM student_calendar_events WHERE provider <> 'google'" in sql
    assert "WHERE provider <> 'google' OR oauth_attempt_id IS NULL" in sql
    assert sql.index("Provider calendar receipts prevent this downgrade") < sql.index("DROP COLUMN")
    assert "DELETE FROM" not in sql


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Actual provider migration and downgrade guard require PostgreSQL",
)
async def test_postgres_provider_upgrade_keeps_legacy_proof_and_refuses_non_google_loss(event_env):
    env = event_env
    view = await preview(env)

    def apply(connection, direction):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration(), direction)()

    async with env.factory() as database:
        connection = await database.connection()
        before = (
            await database.execute(
                text("SELECT status, digest, encrypted_payload FROM student_calendar_events")
            )
        ).one()
        await connection.run_sync(lambda value: apply(value, "downgrade"))
        await connection.run_sync(lambda value: apply(value, "upgrade"))
        after = (
            await database.execute(
                text("SELECT status, digest, encrypted_payload FROM student_calendar_events")
            )
        ).one()
        assert tuple(after) == tuple(before)
        assert (
            await database.scalar(text("SELECT provider FROM student_calendar_events")) == "google"
        )
        assert (
            await database.scalar(text("SELECT provider FROM student_calendar_write_grants"))
            == "google"
        )
        await database.execute(
            text("UPDATE student_calendar_events SET provider = 'icloud' WHERE id = :id"),
            {"id": UUID(view["id"])},
        )
        await database.commit()
        connection = await database.connection()
        with pytest.raises(SQLAlchemyError, match="Provider calendar receipts prevent"):
            async with database.begin_nested():
                await connection.run_sync(lambda value: apply(value, "downgrade"))
        assert (
            await database.scalar(text("SELECT provider FROM student_calendar_events")) == "icloud"
        )
