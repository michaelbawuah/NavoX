"""Actual isolated SQLite migration/catalog parity; no production database access."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from navox.db.vox_usage import VoxUsageDelta, VoxUsageEvent, VoxUsagePolicy, VoxUsageStream


def test_usage_migration_matches_models_preserves_quota_from_call_cleanup_and_refuses_loss():
    path = Path(__file__).parents[1] / "migrations/versions/0053_vox_usage.py"
    spec = importlib.util.spec_from_file_location("usage_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0053_vox_usage"
    assert module.down_revision == "0052_student_calendar_events"
    engine = create_engine("sqlite://")
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
        catalog = inspect(connection)
        for model in (VoxUsageStream, VoxUsageEvent, VoxUsageDelta, VoxUsagePolicy):
            assert {c["name"] for c in catalog.get_columns(model.__tablename__)} == set(
                model.__table__.columns.keys()
            )
            assert {c["name"] for c in catalog.get_indexes(model.__tablename__)} == {
                i.name for i in model.__table__.indexes
            }
        assert not any(
            fk["referred_table"] == "vox_calls"
            for fk in catalog.get_foreign_keys("vox_usage_streams")
        )
        connection.execute(
            text(
                "INSERT INTO vox_usage_policy(id,revision,definition,updated_at) "
                "VALUES(1,1,'{}',CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(RuntimeError, match="must be preserved"):
            module.downgrade()
        connection.execute(text("DELETE FROM vox_usage_policy"))
        module.downgrade()
        assert not any(
            name.startswith("vox_usage_") for name in inspect(connection).get_table_names()
        )
    engine.dispose()
