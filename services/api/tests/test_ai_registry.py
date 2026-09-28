"""Persistent registry boundaries, including independent async database sessions."""

import asyncio
import json

import pytest
from sqlalchemy import select, update
from test_ai_gateway_foundation import MODEL, make_registry

from navox.ai.foundation.persistence import (
    RegistryConflict,
    RegistryStore,
    canonical,
    digest,
    model_key,
)
from navox.ai.foundation.registry import RegistrySnapshot
from navox.db import models  # noqa: F401
from navox.db.ai_registry import (
    AIModel,
    AIProfileAssignment,
    AIPrompt,
    AIRegistryRevision,
    AIRegistryState,
    AISchema,
)


@pytest.mark.asyncio
async def test_registry_persists_exact_snapshot_and_starts_without_traffic(ai_database):
    snapshot = make_registry()
    async with ai_database() as database:
        assert await RegistryStore(database).load() is None
        saved = await RegistryStore(database).publish(snapshot, expected_revision=0)
        await database.commit()
    async with ai_database() as database:
        assert await RegistryStore(database).load() == snapshot
        assert saved == digest(canonical(snapshot))
        model = await database.get(AIModel, model_key(MODEL))
        assert not model.enabled
        assignment = await database.scalar(select(AIProfileAssignment))
        assert assignment.rollout_percent == 0 and not assignment.shadow_enabled
        assert await database.scalar(select(AIPrompt.name)) == snapshot.prompts[0].reference.name
        assert await database.scalar(select(AISchema.name)) == snapshot.schemas[0].reference.name


@pytest.mark.asyncio
async def test_stale_writer_cannot_overwrite_or_partially_publish(ai_database):
    async with ai_database() as database:
        await RegistryStore(database).publish(make_registry(), expected_revision=0)
        await database.commit()

    async def writer(label):
        async with ai_database() as database:
            current = make_registry(revision=2)
            try:
                await RegistryStore(database).publish(current, expected_revision=1)
                await database.commit()
                return label
            except RegistryConflict:
                await database.rollback()
                return "conflict"

    results = await asyncio.gather(writer("a"), writer("b"))
    assert results.count("conflict") == 1
    async with ai_database() as database:
        assert (await RegistryStore(database).load()).revision == 2
        assert len((await database.scalars(select(AIRegistryRevision))).all()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["prompts", "schemas"])
async def test_published_versions_cannot_be_deleted(ai_database, field):
    async with ai_database() as database:
        await RegistryStore(database).publish(make_registry(), expected_revision=0)
        await database.commit()
        values = json.loads(canonical(make_registry(revision=2)))
        values[field] = []
        if field == "schemas":
            values["prompts"] = []
        with pytest.raises(ValueError, match="cannot be removed"):
            await RegistryStore(database).publish(
                RegistrySnapshot.model_validate(values), expected_revision=1
            )
        assert (await RegistryStore(database).load()).revision == 1


@pytest.mark.asyncio
async def test_registry_rollback_and_retired_models(ai_database):
    async with ai_database() as database:
        await RegistryStore(database).publish(make_registry(), expected_revision=0)
        await database.commit()
        await RegistryStore(database).publish(
            make_registry(revision=2, models=(), profiles=()), expected_revision=1
        )
        await database.rollback()
    async with ai_database() as database:
        assert (await RegistryStore(database).load()).revision == 1
        await RegistryStore(database).publish(
            make_registry(revision=2, models=(), profiles=()), expected_revision=1
        )
        await database.commit()
    async with ai_database() as database:
        assert (await RegistryStore(database).load(1)).models[0].reference == MODEL
        assert not (await database.get(AIModel, model_key(MODEL))).enabled
        assert not (await database.scalars(select(AIProfileAssignment))).all()


@pytest.mark.asyncio
async def test_storage_corruption_fails_closed(ai_database):
    async with ai_database() as database:
        await RegistryStore(database).publish(make_registry(), expected_revision=0)
        await database.commit()
        await database.execute(update(AIRegistryRevision).values(snapshot='{"revision":1}'))
        with pytest.raises(RegistryConflict, match="corrupted"):
            await RegistryStore(database).load()


@pytest.mark.asyncio
async def test_projection_error_rolls_back_whole_revision(ai_database, monkeypatch):
    async with ai_database() as database:
        store = RegistryStore(database)

        async def fail(_):
            raise RuntimeError("fixture failure")

        monkeypatch.setattr(store, "_project", fail)
        with pytest.raises(RuntimeError, match="fixture"):
            await store.publish(make_registry(), expected_revision=0)
        await database.commit()
    async with ai_database() as database:
        assert await RegistryStore(database).load() is None
        assert await database.get(AIRegistryState, 1) is None


def test_canonical_registry_is_stable_across_set_orders():
    first = make_registry()
    values = json.loads(canonical(first))
    values["models"][0]["capabilities"].reverse()
    second = RegistrySnapshot.model_validate(values)
    assert canonical(first) == canonical(second)
