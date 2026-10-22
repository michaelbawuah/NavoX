"""Exercise additive media DDL, terminal-state guards and independent cleanup storage."""

import importlib.util
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, inspect, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.schema import CreateIndex
from test_vox_migration import call
from test_vox_migration import database as base_database  # noqa: F401 - shared SQLite fixture

from navox.db.base import Base
from navox.db.models import User, UserSession, Workspace
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
MODELS = (VoxMediaConnection, VoxMediaCleanup)


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0049_vox_media_connections.py"
    spec = importlib.util.spec_from_file_location("navox_vox_media_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(direction, dialect="postgresql"):
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name=dialect,
        opts={"as_sql": True, "output_buffer": output, "literal_binds": True},
    )
    with Operations.context(context):
        getattr(migration(), direction)()
    return " ".join(output.getvalue().split())


def run_migration(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration(), direction)()


def epoch(owner, **changes):
    identifier = uuid4()
    return {
        **owner,
        "id": identifier,
        "request_id": uuid4(),
        "generation": 0,
        "status": "preparing",
        "room_name": f"vox-{identifier.hex}",
        "client_identity": f"client-{identifier.hex}",
        "agent_identity": f"agent-{identifier.hex}",
        "created_at": NOW,
        "expires_at": NOW + timedelta(seconds=120),
        "confirmed_at": None,
        "revoked_at": None,
        "reason": None,
        **changes,
    }


def cleanup(connection=None, **changes):
    value = connection or epoch({"owner_scope_id": uuid4()})
    return {
        "id": uuid4(),
        "connection_id": value["id"],
        "owner_scope_id": value["owner_scope_id"],
        "room_name": value["room_name"],
        "client_identity": value["client_identity"],
        "agent_identity": value["agent_identity"],
        "connection_expires_at": value["expires_at"],
        "created_at": value["created_at"],
        "status": "armed",
        "requested_at": None,
        "next_attempt_at": None,
        "claim_id": None,
        "claim_expires_at": None,
        "attempts": 0,
        "completed_at": None,
        **changes,
    }


def seed_old(connection, owner):
    """Store real 0048 rows before creating either new media table."""
    scope, call_id = uuid4(), uuid4()
    connection.execute(
        VoxState.__table__.insert(),
        {
            "id": scope,
            "user_id": owner["user_id"],
            "workspace_id": owner["workspace_id"],
            "generation": 0,
        },
    )
    connection.execute(VoxCall.__table__.insert(), call(owner, id=call_id))
    return {**owner, "owner_scope_id": scope, "call_id": call_id}


@pytest.fixture
def media_database(base_database):  # noqa: F811 - shared fixture import
    engine, owner = base_database
    with engine.begin() as connection:
        owned = seed_old(connection, owner)
        old_tables = set(inspect(connection).get_table_names())
        before = {
            table.name: list(connection.execute(select(table)).mappings())
            for table in (
                User.__table__,
                Workspace.__table__,
                UserSession.__table__,
                VoxState.__table__,
                VoxCall.__table__,
            )
        }
        run_migration(connection, "upgrade")
    yield engine, owned, old_tables, before


def test_media_upgrade_is_additive_and_opaque_with_independent_cleanup():
    assert migration().revision == "0049_vox_media_connections"
    assert migration().down_revision == "0048_vox_calls"
    sql = render("upgrade")
    assert "ALTER TABLE" not in sql and "DROP" not in sql and "INSERT INTO" not in sql
    for model in MODELS:
        table = model.__table__
        assert Base.metadata.tables[table.name] is table
        assert f"CREATE TABLE {table.name}" in sql
        for column in table.columns:
            assert f"{column.name} " in sql
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                assert f"CONSTRAINT {constraint.name}" in sql
                assert " ".join(str(constraint.sqltext).split()) in sql
            if isinstance(constraint, UniqueConstraint) and constraint.name:
                assert f"CONSTRAINT {constraint.name}" in sql
        for index in table.indexes:
            assert (
                " ".join(str(CreateIndex(index).compile(dialect=postgresql.dialect())).split())
                in sql
            )
    assert all(
        constraint.ondelete == "CASCADE"
        for constraint in VoxMediaConnection.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )
    assert len(VoxMediaConnection.__table__.foreign_keys) == 5
    assert not VoxMediaCleanup.__table__.foreign_keys
    assert set(VoxMediaCleanup.__table__.columns.keys()) == {
        "id",
        "connection_id",
        "owner_scope_id",
        "room_name",
        "client_identity",
        "agent_identity",
        "connection_expires_at",
        "created_at",
        "status",
        "requested_at",
        "next_attempt_at",
        "claim_id",
        "claim_expires_at",
        "attempts",
        "completed_at",
    }
    assert "TIMESTAMP WITH TIME ZONE" in sql


@pytest.mark.parametrize("dialect", ["postgresql", "sqlite"])
def test_only_one_nonterminal_media_epoch_is_unique_per_owner(dialect):
    index = next(
        index
        for index in VoxMediaConnection.__table__.indexes
        if index.name == "uq_vox_media_current_owner"
    )
    compiled = str(
        CreateIndex(index).compile(
            dialect=postgresql.dialect() if dialect == "postgresql" else sqlite.dialect(),
        )
    )
    expected = (
        "CREATE UNIQUE INDEX uq_vox_media_current_owner ON vox_media_connections "
        "(owner_scope_id) WHERE status IN ('preparing', 'confirmed')"
    )
    assert " ".join(compiled.split()) == expected
    assert expected in render("upgrade", dialect)


def test_downgrade_guard_locks_and_preserves_every_media_or_cleanup_record():
    sql = render("downgrade")
    lock = "LOCK TABLE vox_media_connections, vox_media_cleanup IN ACCESS EXCLUSIVE MODE"
    assert lock in sql
    assert sql.index(lock) < sql.index("IF EXISTS")
    assert "EXISTS (SELECT 1 FROM vox_media_connections)" in sql
    assert "EXISTS (SELECT 1 FROM vox_media_cleanup)" in sql
    assert sql.index("Vox media authority or cleanup prevents this downgrade") < sql.index(
        "DROP TABLE"
    )
    assert "DELETE FROM" not in sql and "TRUNCATE" not in sql


def test_actual_upgrade_preserves_all_old_rows_and_table_shapes(media_database):
    engine, _, old_tables, before = media_database
    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == old_tables | {
            model.__tablename__ for model in MODELS
        }
        for name, rows in before.items():
            assert list(connection.execute(select(Base.metadata.tables[name])).mappings()) == rows
        for model in MODELS:
            table = model.__table__
            actual = inspect(connection)
            assert {item["name"] for item in actual.get_columns(table.name)} == set(
                table.columns.keys()
            )
            assert {item["name"] for item in actual.get_check_constraints(table.name)} == {
                item.name for item in table.constraints if isinstance(item, CheckConstraint)
            }
            assert {
                tuple(item["column_names"]) for item in actual.get_unique_constraints(table.name)
            } == {
                tuple(column.name for column in item.columns)
                for item in table.constraints
                if isinstance(item, UniqueConstraint)
            }
            assert {item["name"] for item in actual.get_indexes(table.name)} == {
                item.name for item in table.indexes
            }
        assert inspect(connection).get_foreign_keys(VoxMediaCleanup.__tablename__) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": -1},
        {"status": "live"},
        {"expires_at": NOW - timedelta(seconds=1)},
        {"status": "confirmed"},
        {"revoked_at": NOW},
        {"reason": "hang-up"},
        {"status": "revoked"},
        {"status": "revoked", "reason": "hang-up"},
        {"status": "revoked", "revoked_at": NOW},
    ],
)
def test_actual_upgrade_rejects_inconsistent_connection_states(media_database, changes):
    engine, owner, _, _ = media_database
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(VoxMediaConnection.__table__.insert(), epoch(owner, **changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "live"},
        {"attempts": -1},
        {"requested_at": NOW},
        {"next_attempt_at": NOW},
        {"claim_id": uuid4()},
        {"claim_expires_at": NOW},
        {"completed_at": NOW},
        {"status": "pending"},
        {"status": "pending", "requested_at": NOW},
        {"status": "pending", "next_attempt_at": NOW},
        {"status": "claimed", "requested_at": NOW, "next_attempt_at": NOW},
        {"status": "claimed", "requested_at": NOW, "next_attempt_at": NOW, "claim_id": uuid4()},
        {"status": "done", "requested_at": NOW},
        {"status": "done", "requested_at": NOW, "completed_at": NOW, "next_attempt_at": NOW},
    ],
)
def test_actual_upgrade_rejects_inconsistent_cleanup_states(media_database, changes):
    engine, _, _, _ = media_database
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(VoxMediaCleanup.__table__.insert(), cleanup(**changes))


@pytest.mark.parametrize("status", ["armed", "pending", "claimed", "done"])
def test_actual_upgrade_accepts_all_valid_cleanup_lifecycle_states(media_database, status):
    engine, _, _, _ = media_database
    fields = {"status": status}
    if status != "armed":
        fields["requested_at"] = NOW
    if status in ("pending", "claimed"):
        fields["next_attempt_at"] = NOW
    if status == "claimed":
        fields.update(claim_id=uuid4(), claim_expires_at=NOW + timedelta(seconds=30))
    if status == "done":
        fields["completed_at"] = NOW
    with engine.begin() as connection:
        connection.execute(VoxMediaCleanup.__table__.insert(), cleanup(**fields))


def test_current_owner_and_retried_request_uniqueness_are_enforced_after_revocation(media_database):
    engine, owner, _, _ = media_database
    first = epoch(owner)
    with engine.begin() as connection:
        connection.execute(VoxMediaConnection.__table__.insert(), first)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(VoxMediaConnection.__table__.insert(), epoch(owner))
    with engine.begin() as connection:
        connection.execute(
            VoxMediaConnection.__table__.update()
            .where(
                VoxMediaConnection.id == first["id"],
            )
            .values(status="revoked", revoked_at=NOW, reason="hang-up")
        )
        connection.execute(VoxMediaConnection.__table__.insert(), epoch(owner))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            VoxMediaConnection.__table__.insert(),
            epoch(
                owner,
                request_id=first["request_id"],
                status="revoked",
                revoked_at=NOW,
                reason="hang-up",
            ),
        )


@pytest.mark.parametrize("parent", ["user", "workspace", "session", "call", "scope"])
def test_parent_cascade_erases_connection_but_preserves_exact_cleanup_target(
    media_database, parent
):
    engine, owner, _, _ = media_database
    value = epoch(owner)
    pending = cleanup(value)
    table, identifier = {
        "user": (User, owner["user_id"]),
        "workspace": (Workspace, owner["workspace_id"]),
        "session": (UserSession, owner["auth_session_id"]),
        "call": (VoxCall, owner["call_id"]),
        "scope": (VoxState, owner["owner_scope_id"]),
    }[parent]
    with engine.begin() as connection:
        connection.execute(VoxMediaConnection.__table__.insert(), value)
        connection.execute(VoxMediaCleanup.__table__.insert(), pending)
        before = connection.execute(select(VoxMediaCleanup)).mappings().one()
        connection.execute(table.__table__.delete().where(table.id == identifier))
        assert connection.execute(select(VoxMediaConnection)).first() is None
        assert connection.execute(select(VoxMediaCleanup)).mappings().one() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["empty", "connection", "armed", "pending", "claimed", "done"])
async def test_postgres_upgrade_and_guarded_downgrade_preserve_old_data(ai_database, content):
    """DDL runs only in the fixture's disposable schema, never the configured public schema."""
    engine = ai_database.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL transactional DDL and the downgrade guard")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.drop_all(
                sync,
                tables=[model.__table__ for model in MODELS],
            )
        )
        owner = {"user_id": uuid4(), "workspace_id": uuid4(), "auth_session_id": uuid4()}

        def prepare(sync):
            sync.execute(
                User.__table__.insert(), {"id": owner["user_id"], "email": "media-ddl@example.test"}
            )
            sync.execute(
                Workspace.__table__.insert(), {"id": owner["workspace_id"], "name": "Synthetic DDL"}
            )
            sync.execute(
                UserSession.__table__.insert(),
                {
                    "id": owner["auth_session_id"],
                    "user_id": owner["user_id"],
                    "token_hash": "synthetic-media-ddl",
                    "expires_at": NOW + timedelta(hours=1),
                },
            )
            owned = seed_old(sync, owner)
            before = sync.execute(select(VoxCall)).mappings().one()
            run_migration(sync, "upgrade")
            assert sync.execute(select(VoxCall)).mappings().one() == before
            value = epoch(owned, status="revoked", revoked_at=NOW, reason="expired")
            if content == "connection":
                sync.execute(VoxMediaConnection.__table__.insert(), value)
            elif content != "empty":
                fields = {"status": content}
                if content != "armed":
                    fields["requested_at"] = NOW
                if content in ("pending", "claimed"):
                    fields["next_attempt_at"] = NOW
                if content == "claimed":
                    fields.update(claim_id=uuid4(), claim_expires_at=NOW + timedelta(seconds=30))
                if content == "done":
                    fields["completed_at"] = NOW
                sync.execute(VoxMediaCleanup.__table__.insert(), cleanup(value, **fields))
            return before

        before = await connection.run_sync(prepare)
        if content == "empty":
            await connection.run_sync(lambda sync: run_migration(sync, "downgrade"))
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert not {model.__tablename__ for model in MODELS} & tables
        else:
            with pytest.raises(
                DBAPIError, match="Vox media authority or cleanup prevents this downgrade"
            ):
                async with connection.begin_nested():
                    await connection.run_sync(lambda sync: run_migration(sync, "downgrade"))
            for model in MODELS:
                assert await connection.run_sync(
                    lambda sync, table=model.__tablename__: inspect(sync).has_table(table)
                )
            model = VoxMediaConnection if content == "connection" else VoxMediaCleanup
            assert len((await connection.execute(select(model))).all()) == 1
        assert (await connection.execute(select(VoxCall))).mappings().one() == before
