"""Additive consent DDL and non-destructive downgrade on real SQLite tables."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, text

from navox.db.base import Base
from navox.db.models import User, Workspace
from navox.db.student_calendars import StudentCalendarGrant, StudentCalendarState


def migration():
    path = (
        Path(__file__).resolve().parents[1] / "migrations/versions/0051_student_calendar_consent.py"
    )
    spec = importlib.util.spec_from_file_location("student_calendar_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(connection)
        StudentCalendarGrant.__table__.drop(connection)
        StudentCalendarState.__table__.drop(connection)
        user_id, workspace_id = uuid4(), uuid4()
        connection.execute(
            User.__table__.insert().values(
                id=user_id,
                email="migration@example.com",
                timezone="America/New_York",
                agent_paused=False,
            )
        )
        connection.execute(
            Workspace.__table__.insert().values(
                id=workspace_id, name="Migration", workspace_type="personal"
            )
        )
        yield connection, user_id, workspace_id
    engine.dispose()


def apply(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration(), direction)()


def test_upgrade_preserves_existing_records_and_matches_model_contract(database):
    connection, user_id, _ = database
    before = dict(
        connection.execute(select(User.__table__).where(User.id == user_id)).mappings().one()
    )
    old_tables = set(inspect(connection).get_table_names())
    apply(connection, "upgrade")
    assert migration().down_revision == "0050_vox_provision_attempts"
    assert set(inspect(connection).get_table_names()) - old_tables == {
        "student_calendar_states",
        "student_calendar_grants",
    }
    assert (
        dict(connection.execute(select(User.__table__).where(User.id == user_id)).mappings().one())
        == before
    )
    for model in (StudentCalendarState, StudentCalendarGrant):
        actual = inspect(connection)
        assert {column["name"] for column in actual.get_columns(model.__tablename__)} == set(
            model.__table__.columns.keys()
        )
        assert {index["name"] for index in actual.get_indexes(model.__tablename__)} == {
            index.name for index in model.__table__.indexes
        }
        assert {check["name"] for check in actual.get_check_constraints(model.__tablename__)} == {
            constraint.name
            for constraint in model.__table__.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }


def test_retained_reset_generation_prevents_destructive_downgrade(database):
    connection, user_id, workspace_id = database
    apply(connection, "upgrade")
    identifier = uuid4()
    connection.execute(
        StudentCalendarState.__table__.insert().values(
            id=identifier, user_id=user_id, workspace_id=workspace_id, generation=1, revision=1
        )
    )
    with pytest.raises(RuntimeError, match="downgrade refused"):
        apply(connection, "downgrade")
    assert (
        connection.execute(
            select(StudentCalendarState.generation).where(StudentCalendarState.id == identifier)
        ).scalar_one()
        == 1
    )
    assert "student_calendar_grants" in inspect(connection).get_table_names()


def test_empty_downgrade_removes_only_new_tables(database):
    connection, user_id, _ = database
    apply(connection, "upgrade")
    apply(connection, "downgrade")
    assert not {"student_calendar_states", "student_calendar_grants"} & set(
        inspect(connection).get_table_names()
    )
    assert connection.execute(select(User.id).where(User.id == user_id)).scalar_one() == user_id


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Actual PostgreSQL migration/lock guard requires the isolated test database",
)
async def test_postgres_additive_migration_and_retained_reset_guard(ai_database):
    async with ai_database() as database:
        await database.execute(text("DROP TABLE student_calendar_grants"))
        await database.execute(text("DROP TABLE student_calendar_states"))
        user_id, workspace_id = uuid4(), uuid4()
        database.add(
            User(id=user_id, email="pg-migration@example.com", timezone="UTC", agent_paused=False)
        )
        database.add(Workspace(id=workspace_id, name="PG migration", workspace_type="personal"))
        await database.commit()
        connection = await database.connection()
        before = (
            (await database.execute(select(User.__table__).where(User.id == user_id)))
            .mappings()
            .one()
        )
        await connection.run_sync(lambda value: apply(value, "upgrade"))
        after = (
            (await database.execute(select(User.__table__).where(User.id == user_id)))
            .mappings()
            .one()
        )
        assert dict(before) == dict(after)
        state = StudentCalendarState(
            user_id=user_id, workspace_id=workspace_id, generation=1, revision=1
        )
        database.add(state)
        await database.flush()
        async with database.begin_nested():
            with pytest.raises(RuntimeError, match="downgrade refused"):
                await connection.run_sync(lambda value: apply(value, "downgrade"))
        assert (
            await database.scalar(
                select(StudentCalendarState.generation).where(StudentCalendarState.id == state.id)
            )
            == 1
        )
        await database.commit()
