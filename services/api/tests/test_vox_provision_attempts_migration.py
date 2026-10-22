"""Actual additive 0049→0050 DDL and durable, content-free provisioning evidence."""

import hashlib
import importlib.util
import io
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.schema import CreateIndex
from test_vox_media_migration import NOW, cleanup, epoch, seed_old
from test_vox_media_migration import media_database as shared_media_database
from test_vox_migration import database as base_database  # noqa: F401 - shared fixture dependency

from navox.db.base import Base
from navox.db.models import User, UserSession, Workspace
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.db.vox_provision import VoxMediaProvisionAttempt

media_database = shared_media_database
TABLE = VoxMediaProvisionAttempt.__table__
OLD_TABLES = (
    User.__table__,
    Workspace.__table__,
    UserSession.__table__,
    VoxState.__table__,
    VoxCall.__table__,
    VoxMediaConnection.__table__,
    VoxMediaCleanup.__table__,
)


def migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/0050_vox_media_provision_attempts.py"
    )
    spec = importlib.util.spec_from_file_location("navox_vox_provision_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_migration(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration(), direction)()


def render(direction):
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output, "literal_binds": True},
    )
    with Operations.context(context):
        getattr(migration(), direction)()
    return " ".join(output.getvalue().split())


def attempt(connection_id=None, **changes):
    return {
        "id": uuid4(),
        "connection_id": connection_id or uuid4(),
        "status": "in-flight",
        "started_at": NOW,
        "finished_at": None,
        **changes,
    }


def snapshot(connection):
    """Fingerprint existing rows and inspect actual DDL, independently of the new model."""
    inspector = inspect(connection)
    result = {}
    for table in OLD_TABLES:
        name = table.name
        rows = [dict(row) for row in connection.execute(select(table)).mappings()]
        serialized = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
        shape = {
            "columns": inspector.get_columns(name),
            "checks": inspector.get_check_constraints(name),
            "unique": inspector.get_unique_constraints(name),
            "indexes": inspector.get_indexes(name),
            "foreign_keys": inspector.get_foreign_keys(name),
        }
        result[name] = {
            "rows_sha256": hashlib.sha256(json.dumps(serialized).encode()).hexdigest(),
            "shape_sha256": hashlib.sha256(
                json.dumps(shape, sort_keys=True, default=str).encode()
            ).hexdigest(),
        }
    return result


@pytest.fixture
def provision_database(media_database):
    engine, owner, _, _ = media_database
    value = epoch(owner)
    with engine.begin() as connection:
        connection.execute(VoxMediaConnection.__table__.insert(), value)
        connection.execute(VoxMediaCleanup.__table__.insert(), cleanup(value))
        before = snapshot(connection)
        run_migration(connection, "upgrade")
    return engine, owner, value, before


def test_migration_model_parity_and_independent_minimal_evidence():
    assert migration().revision == "0050_vox_provision_attempts"
    assert migration().down_revision == "0049_vox_media_connections"
    assert Base.metadata.tables[TABLE.name] is TABLE
    assert not TABLE.foreign_keys
    assert set(TABLE.columns.keys()) == {
        "id",
        "connection_id",
        "status",
        "started_at",
        "finished_at",
    }
    sql = render("upgrade")
    assert f"CREATE TABLE {TABLE.name}" in sql
    assert "ALTER TABLE" not in sql and "DROP" not in sql and "INSERT INTO" not in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql and "UNIQUE (connection_id)" in sql
    for constraint in TABLE.constraints:
        if isinstance(constraint, CheckConstraint):
            assert f"CONSTRAINT {constraint.name}" in sql
            assert " ".join(str(constraint.sqltext).split()) in sql
    for index in TABLE.indexes:
        assert (
            " ".join(str(CreateIndex(index).compile(dialect=postgresql.dialect())).split()) in sql
        )


def test_actual_additive_upgrade_preserves_old_fingerprints_and_matches_ddl(provision_database):
    engine, _, _, before = provision_database
    with engine.connect() as connection:
        assert snapshot(connection) == before
        actual = inspect(connection)
        assert set(actual.get_table_names()) == set(before) | {TABLE.name}
        assert {item["name"] for item in actual.get_columns(TABLE.name)} == set(
            TABLE.columns.keys()
        )
        assert {item["name"] for item in actual.get_check_constraints(TABLE.name)} == {
            item.name for item in TABLE.constraints if isinstance(item, CheckConstraint)
        }
        assert {
            tuple(item["column_names"]) for item in actual.get_unique_constraints(TABLE.name)
        } == {
            tuple(column.name for column in item.columns)
            for item in TABLE.constraints
            if isinstance(item, UniqueConstraint)
        }
        assert {item["name"] for item in actual.get_indexes(TABLE.name)} == {
            item.name for item in TABLE.indexes
        }
        assert actual.get_foreign_keys(TABLE.name) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "done"},
        {"finished_at": NOW},
        {"status": "uncertain"},
        {"status": "acknowledged"},
        {"status": "uncertain", "finished_at": NOW - timedelta(seconds=1)},
    ],
)
def test_actual_migration_rejects_invalid_evidence(provision_database, changes):
    engine, _, _, _ = provision_database
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(TABLE.insert(), attempt(**changes))


@pytest.mark.parametrize("status", ["in-flight", "acknowledged", "uncertain"])
def test_attempt_is_unique_for_the_epoch_even_after_known_or_unknown_completion(
    provision_database, status
):
    engine, _, value, _ = provision_database
    fields = {} if status == "in-flight" else {"finished_at": NOW + timedelta(seconds=1)}
    first = attempt(value["id"], status=status, **fields)
    with engine.begin() as connection:
        connection.execute(TABLE.insert(), first)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(TABLE.insert(), attempt(value["id"]))
    with engine.connect() as connection:
        assert connection.execute(select(TABLE)).mappings().one()["id"] == first["id"]


@pytest.mark.parametrize("parent", ["user", "workspace", "session", "call", "scope", "cleanup"])
def test_owner_and_cleanup_deletion_cannot_erase_uncertain_provider_evidence(
    provision_database, parent
):
    engine, owner, value, _ = provision_database
    parent_table, identifier = {
        "user": (User.__table__, owner["user_id"]),
        "workspace": (Workspace.__table__, owner["workspace_id"]),
        "session": (UserSession.__table__, owner["auth_session_id"]),
        "call": (VoxCall.__table__, owner["call_id"]),
        "scope": (VoxState.__table__, owner["owner_scope_id"]),
        "cleanup": (VoxMediaCleanup.__table__, None),
    }[parent]
    with engine.begin() as connection:
        connection.execute(
            TABLE.insert(),
            attempt(value["id"], status="uncertain", finished_at=NOW + timedelta(seconds=1)),
        )
        before = connection.execute(select(TABLE)).mappings().one()
        deletion = parent_table.delete()
        if identifier is not None:
            deletion = deletion.where(parent_table.c.id == identifier)
        connection.execute(deletion)
        assert connection.execute(select(TABLE)).mappings().one() == before
        if parent != "cleanup":
            assert connection.execute(select(VoxMediaConnection)).first() is None
            assert connection.execute(select(VoxMediaCleanup)).first() is not None


def test_downgrade_requires_an_empty_locked_evidence_table():
    sql = render("downgrade")
    lock = "LOCK TABLE vox_media_provision_attempts IN ACCESS EXCLUSIVE MODE"
    assert lock in sql
    assert sql.index(lock) < sql.index("IF EXISTS")
    assert sql.index("Provider provisioning evidence prevents this downgrade") < sql.index(
        "DROP TABLE"
    )
    assert "DELETE FROM" not in sql and "TRUNCATE" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["empty", "in-flight", "acknowledged", "uncertain"])
async def test_postgres_actual_upgrade_and_protected_downgrade_preserve_all_0049_rows(
    ai_database, content
):
    engine = ai_database.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL transactional DDL and protected downgrade")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: TABLE.drop(sync))

        def prepare(sync):
            owner = {"user_id": uuid4(), "workspace_id": uuid4(), "auth_session_id": uuid4()}
            sync.execute(
                User.__table__.insert(), {"id": owner["user_id"], "email": "attempt@example.test"}
            )
            sync.execute(
                Workspace.__table__.insert(), {"id": owner["workspace_id"], "name": "Synthetic DDL"}
            )
            sync.execute(
                UserSession.__table__.insert(),
                {
                    "id": owner["auth_session_id"],
                    "user_id": owner["user_id"],
                    "token_hash": "synthetic-attempt-ddl",
                    "expires_at": NOW + timedelta(hours=1),
                },
            )
            owned = seed_old(sync, owner)
            value = epoch(owned)
            sync.execute(VoxMediaConnection.__table__.insert(), value)
            sync.execute(VoxMediaCleanup.__table__.insert(), cleanup(value))
            before = snapshot(sync)
            before_tables = set(inspect(sync).get_table_names())
            run_migration(sync, "upgrade")
            assert snapshot(sync) == before
            assert set(inspect(sync).get_table_names()) == before_tables | {TABLE.name}
            if content != "empty":
                fields = {} if content == "in-flight" else {"finished_at": NOW}
                sync.execute(TABLE.insert(), attempt(value["id"], status=content, **fields))
            return before

        before = await connection.run_sync(prepare)
        if content == "empty":
            await connection.run_sync(lambda sync: run_migration(sync, "downgrade"))
            assert not await connection.run_sync(lambda sync: inspect(sync).has_table(TABLE.name))
        else:
            rows = (await connection.execute(select(TABLE))).mappings().all()
            with pytest.raises(
                DBAPIError, match="Provider provisioning evidence prevents this downgrade"
            ):
                async with connection.begin_nested():
                    await connection.run_sync(lambda sync: run_migration(sync, "downgrade"))
            assert (await connection.execute(select(TABLE))).mappings().all() == rows
        assert await connection.run_sync(snapshot) == before
