"""Vox DDL parity and actual constraint enforcement without provider access."""

import importlib.util
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
    create_engine,
    inspect,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex

from navox.db.base import Base
from navox.db.models import User, UserSession, Workspace
from navox.db.vox import VoxCall, VoxState

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0048_vox_calls.py"
    spec = importlib.util.spec_from_file_location("navox_vox_migration", path)
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


def test_vox_upgrade_is_additive_and_matches_content_free_owned_metadata():
    assert migration().revision == "0048_vox_calls"
    assert migration().down_revision == "0047_saved_boards"
    sql = render("upgrade")
    for model in (VoxState, VoxCall):
        table = model.__table__
        assert Base.metadata.tables[table.name] is table
        assert f"CREATE TABLE {table.name}" in sql
        for column in table.columns:
            assert f"{column.name} " in sql
        for index in table.indexes:
            statement = " ".join(
                str(CreateIndex(index).compile(dialect=postgresql.dialect())).split()
            )
            assert statement in sql
        for constraint in table.constraints:
            if isinstance(constraint, (UniqueConstraint, CheckConstraint)):
                assert f"CONSTRAINT {constraint.name}" in sql
                if isinstance(constraint, CheckConstraint):
                    assert " ".join(str(constraint.sqltext).split()) in sql
            if isinstance(constraint, ForeignKeyConstraint):
                assert constraint.ondelete == "CASCADE"
        assert table.columns.user_id.foreign_keys and table.columns.workspace_id.foreign_keys
    assert "FOREIGN KEY(auth_session_id) REFERENCES user_sessions (id) ON DELETE CASCADE" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "ALTER TABLE" not in sql and "DROP" not in sql and "INSERT INTO" not in sql
    assert set(VoxState.__table__.columns.keys()) == {"id", "user_id", "workspace_id", "generation"}
    assert set(VoxCall.__table__.columns.keys()) == {
        "id",
        "user_id",
        "workspace_id",
        "auth_session_id",
        "client_call_id",
        "generation",
        "status",
        "end_reason",
        "created_at",
        "expires_at",
        "ended_at",
    }


@pytest.mark.parametrize("dialect", ["postgresql", "sqlite"])
def test_only_reserved_owner_is_unique_in_both_migration_and_orm_partial_indexes(dialect):
    index = next(
        index for index in VoxCall.__table__.indexes if index.name == "uq_vox_reserved_owner"
    )
    compiled = str(
        CreateIndex(index).compile(
            dialect=postgresql.dialect() if dialect == "postgresql" else sqlite.dialect()
        )
    )
    expected = "CREATE UNIQUE INDEX uq_vox_reserved_owner ON vox_calls (user_id, workspace_id) "
    expected += "WHERE status = 'reserved'"
    assert " ".join(compiled.split()) == expected
    assert expected in render("upgrade", dialect)


def test_vox_downgrade_locks_and_guards_all_call_and_generation_data_before_any_drop():
    sql = render("downgrade")
    lock = "LOCK TABLE vox_calls, vox_states IN ACCESS EXCLUSIVE MODE"
    assert lock in sql
    for model in (VoxState, VoxCall):
        assert f"EXISTS (SELECT 1 FROM {model.__tablename__})" in sql
    assert sql.index(lock) < sql.index("IF EXISTS")
    assert sql.index("Vox reservation data prevents this downgrade") < sql.index("DROP TABLE")
    assert sql.index("DROP TABLE vox_calls") < sql.index("DROP TABLE vox_states")
    assert "DELETE FROM" not in sql and "TRUNCATE" not in sql


@pytest.fixture
def database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    owner = {"user_id": uuid4(), "workspace_id": uuid4(), "auth_session_id": uuid4()}
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(
            connection,
            tables=[
                User.__table__,
                Workspace.__table__,
                UserSession.__table__,
            ],
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
        connection.execute(
            User.__table__.insert(), {"id": owner["user_id"], "email": "vox@example.test"}
        )
        connection.execute(
            Workspace.__table__.insert(),
            {
                "id": owner["workspace_id"],
                "name": "Synthetic owner",
            },
        )
        connection.execute(
            UserSession.__table__.insert(),
            {
                "id": owner["auth_session_id"],
                "user_id": owner["user_id"],
                "token_hash": "synthetic-session-hash",
                "expires_at": NOW + timedelta(hours=1),
            },
        )
    yield engine, owner
    engine.dispose()


def call(owner, **changes):
    return {
        **owner,
        "id": uuid4(),
        "client_call_id": uuid4(),
        "generation": 0,
        "status": "reserved",
        "end_reason": None,
        "created_at": NOW,
        "expires_at": NOW + timedelta(seconds=120),
        "ended_at": None,
        **changes,
    }


def test_sqlite_upgrade_column_constraints_and_indexes_match_model_metadata(database):
    engine, _ = database
    actual = inspect(engine)
    for model in (VoxState, VoxCall):
        table = model.__table__
        assert {column["name"] for column in actual.get_columns(table.name)} == set(
            table.columns.keys()
        )
        assert {item["name"] for item in actual.get_check_constraints(table.name)} == {
            item.name for item in table.constraints if isinstance(item, CheckConstraint)
        }
        assert {item["name"] for item in actual.get_unique_constraints(table.name)} == {
            item.name for item in table.constraints if isinstance(item, UniqueConstraint)
        }
        assert {item["name"] for item in actual.get_indexes(table.name)} == {
            index.name for index in table.indexes
        }
        assert all(
            item["options"]["ondelete"] == "CASCADE" for item in actual.get_foreign_keys(table.name)
        )


def test_reserved_owner_partial_index_prevents_a_second_active_call_even_for_another_session(
    database,
):
    engine, owner = database
    first = call(owner)
    with engine.begin() as connection:
        connection.execute(VoxCall.__table__.insert(), first)
    second_session = uuid4()
    with engine.begin() as connection:
        connection.execute(
            UserSession.__table__.insert(),
            {
                "id": second_session,
                "user_id": owner["user_id"],
                "token_hash": "another-hash",
                "expires_at": NOW + timedelta(hours=1),
            },
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(VoxCall.__table__.insert(), call(owner, auth_session_id=second_session))
    with engine.begin() as connection:
        connection.execute(
            VoxCall.__table__.update()
            .where(VoxCall.id == first["id"])
            .values(
                status="ended",
                end_reason="hang-up",
                ended_at=NOW + timedelta(seconds=1),
            )
        )
        connection.execute(VoxCall.__table__.insert(), call(owner, auth_session_id=second_session))
        assert len(connection.execute(VoxCall.__table__.select()).all()) == 2


def test_ended_client_call_id_remains_unique_for_its_owner(database):
    engine, owner = database
    first = call(owner, status="ended", end_reason="expired", ended_at=NOW + timedelta(seconds=120))
    with engine.begin() as connection:
        connection.execute(VoxCall.__table__.insert(), first)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            VoxCall.__table__.insert(), call(owner, client_call_id=first["client_call_id"])
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": -1},
        {"status": "connected"},
        {"end_reason": "custom"},
        {"end_reason": "hang-up"},
        {"ended_at": NOW},
        {"status": "ended"},
        {"status": "ended", "end_reason": "expired"},
        {"status": "ended", "ended_at": NOW},
        {"expires_at": NOW - timedelta(seconds=1)},
    ],
)
def test_actual_migration_rejects_impossible_call_states(database, changes):
    engine, owner = database
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(VoxCall.__table__.insert(), call(owner, **changes))


@pytest.mark.parametrize("reason", ["hang-up", "sign-out", "expired"])
def test_actual_migration_accepts_only_content_free_terminal_reasons(database, reason):
    engine, owner = database
    with engine.begin() as connection:
        connection.execute(
            VoxCall.__table__.insert(),
            call(
                owner,
                status="ended",
                end_reason=reason,
                ended_at=NOW + timedelta(seconds=1),
            ),
        )


def test_generation_state_has_one_nonnegative_fence_per_owner(database):
    engine, owner = database
    state = {
        "id": uuid4(),
        "user_id": owner["user_id"],
        "workspace_id": owner["workspace_id"],
        "generation": 0,
    }
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(VoxState.__table__.insert(), {**state, "generation": -1})
    with engine.begin() as connection:
        connection.execute(VoxState.__table__.insert(), state)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(VoxState.__table__.insert(), {**state, "id": uuid4(), "generation": 1})


@pytest.mark.parametrize("parent", ["user", "workspace", "session"])
def test_owner_and_auth_session_erasure_cascade_call_metadata_without_retaining_content(
    database, parent
):
    engine, owner = database
    with engine.begin() as connection:
        connection.execute(
            VoxState.__table__.insert(),
            {
                "id": uuid4(),
                "user_id": owner["user_id"],
                "workspace_id": owner["workspace_id"],
                "generation": 1,
            },
        )
        connection.execute(VoxCall.__table__.insert(), call(owner))
        if parent == "user":
            connection.execute(User.__table__.delete().where(User.id == owner["user_id"]))
        elif parent == "workspace":
            connection.execute(
                Workspace.__table__.delete().where(Workspace.id == owner["workspace_id"])
            )
        else:
            connection.execute(
                UserSession.__table__.delete().where(UserSession.id == owner["auth_session_id"])
            )
        assert connection.execute(VoxCall.__table__.select()).first() is None
        states = connection.execute(VoxState.__table__.select()).all()
        assert len(states) == (1 if parent == "session" else 0)
