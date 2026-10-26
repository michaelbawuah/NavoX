"""Scheduling DDL stays additive and never drops an uncertain event receipt."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

from navox.db.base import Base
from navox.db.models import User, Workspace
from navox.db.student_calendars import (
    StudentCalendarEvent,
    StudentCalendarState,
    StudentCalendarWriteGrant,
)


def migration():
    path = (
        Path(__file__).resolve().parents[1] / "migrations/versions/0052_student_calendar_events.py"
    )
    spec = importlib.util.spec_from_file_location("student_calendar_event_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(connection)
        StudentCalendarEvent.__table__.drop(connection)
        StudentCalendarWriteGrant.__table__.drop(connection)
        user, workspace, state = uuid4(), uuid4(), uuid4()
        connection.execute(
            User.__table__.insert().values(
                id=user,
                email="calendar-migration@example.com",
                timezone="America/New_York",
                agent_paused=False,
            )
        )
        connection.execute(
            Workspace.__table__.insert().values(
                id=workspace,
                name="Calendar migration",
                workspace_type="personal",
            )
        )
        connection.execute(
            StudentCalendarState.__table__.insert().values(
                id=state,
                user_id=user,
                workspace_id=workspace,
                generation=0,
                revision=0,
            )
        )
        yield connection, user, workspace, state
    engine.dispose()


def apply(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration(), direction)()


def legacy_event_table(connection):
    table = Table("student_calendar_events", MetaData(), autoload_with=connection)
    # SQLite reflects generic UUID columns as CHAR. Preserve bind processors,
    # without importing newer columns or their Python defaults into old DDL.
    for column in table.columns:
        column.type = StudentCalendarEvent.__table__.c[column.name].type
    return table


def test_additive_upgrade_preserves_existing_tables_and_matches_models(database):
    connection, user, _, _ = database
    before = dict(
        connection.execute(select(User.__table__).where(User.id == user)).mappings().one()
    )
    tables = set(inspect(connection).get_table_names())
    apply(connection, "upgrade")
    assert migration().down_revision == "0051_student_calendar_consent"
    assert set(inspect(connection).get_table_names()) - tables == {
        "student_calendar_write_grants",
        "student_calendar_events",
    }
    assert (
        dict(connection.execute(select(User.__table__).where(User.id == user)).mappings().one())
        == before
    )
    for model in (StudentCalendarWriteGrant, StudentCalendarEvent):
        actual = inspect(connection)
        # This historical migration creates the original Google schema. Later
        # provider columns/constraints are covered by migration 0064's tests.
        assert {c["name"] for c in actual.get_columns(model.__tablename__)} == set(
            model.__table__.columns.keys()
        ) - {"provider"}
        assert {i["name"] for i in actual.get_indexes(model.__tablename__)} == {
            i.name for i in model.__table__.indexes
        }
        assert {c["name"] for c in actual.get_check_constraints(model.__tablename__)} == {
            c.name for c in model.__table__.constraints if c.__class__.__name__ == "CheckConstraint"
        } - {
            "ck_calendar_write_provider",
            "ck_calendar_write_authorization_kind",
            "ck_calendar_event_provider",
        }


def test_empty_downgrade_removes_only_new_tables(database):
    connection, user, _, _ = database
    apply(connection, "upgrade")
    apply(connection, "downgrade")
    assert connection.execute(select(User.id).where(User.id == user)).scalar_one() == user
    assert "student_calendar_events" not in inspect(connection).get_table_names()


def test_uncertain_event_blocks_destructive_downgrade(database):
    from datetime import UTC, datetime, timedelta

    connection, user, workspace, state = database
    apply(connection, "upgrade")
    now = datetime.now(UTC)
    event = uuid4()
    connection.execute(
        legacy_event_table(connection)
        .insert()
        .values(
            id=event,
            owner_scope_id=state,
            user_id=user,
            workspace_id=workspace,
            request_id=uuid4(),
            generation=0,
            revision=2,
            grant_revision=2,
            status="uncertain",
            digest="a" * 64,
            encrypted_payload="opaque-held-provider-receipt",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
    )
    with pytest.raises(RuntimeError, match="must be preserved"):
        apply(connection, "downgrade")
    assert (
        connection.execute(
            select(StudentCalendarEvent.status).where(StudentCalendarEvent.id == event)
        ).scalar_one()
        == "uncertain"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Actual PostgreSQL migration guard requires the isolated test database",
)
async def test_postgres_additive_upgrade_and_held_receipt_guard(ai_database):
    from datetime import UTC, datetime, timedelta

    async with ai_database() as database:
        await database.execute(text("DROP TABLE student_calendar_events"))
        await database.execute(text("DROP TABLE student_calendar_write_grants"))
        user_id, workspace_id, state_id = uuid4(), uuid4(), uuid4()
        database.add(
            User(
                id=user_id,
                email="pg-event-migration@example.com",
                timezone="UTC",
                agent_paused=False,
            )
        )
        database.add(
            Workspace(id=workspace_id, name="PG event migration", workspace_type="personal")
        )
        await database.flush()
        database.add(
            StudentCalendarState(
                id=state_id, user_id=user_id, workspace_id=workspace_id, generation=1, revision=1
            )
        )
        await database.commit()
        connection = await database.connection()
        await connection.run_sync(lambda value: apply(value, "upgrade"))
        assert (
            await database.scalar(
                select(StudentCalendarState.generation).where(StudentCalendarState.id == state_id)
            )
            == 1
        )
        now = datetime.now(UTC)
        event_id = uuid4()
        legacy = await connection.run_sync(legacy_event_table)
        await database.execute(
            legacy.insert().values(
                id=event_id,
                owner_scope_id=state_id,
                user_id=user_id,
                workspace_id=workspace_id,
                request_id=uuid4(),
                generation=1,
                revision=2,
                grant_revision=2,
                status="uncertain",
                digest="a" * 64,
                encrypted_payload="opaque-held-receipt",
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            )
        )
        async with database.begin_nested():
            with pytest.raises(RuntimeError, match="must be preserved"):
                await connection.run_sync(lambda value: apply(value, "downgrade"))
        assert (
            await database.scalar(
                select(StudentCalendarEvent.status).where(StudentCalendarEvent.id == event_id)
            )
            == "uncertain"
        )
        await database.commit()
