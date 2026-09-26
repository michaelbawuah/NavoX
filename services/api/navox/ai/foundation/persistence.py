"""Atomic registry publication. Caller owns commit; credentials never enter a snapshot."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.foundation.registry import ModelRef, RegistrySnapshot, validate_registry_update
from navox.ai.validation import compile_schema
from navox.db.ai_registry import (
    AIModel,
    AIModelCapability,
    AIProfile,
    AIProfileAssignment,
    AIPrompt,
    AIProvider,
    AIProviderHealth,
    AIRegistryRevision,
    AIRegistryState,
    AISchema,
)


class RegistryConflict(ValueError):
    pass


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_json_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True)
        )
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical(value: BaseModel) -> str:
    """Stable across processes, including frozenset ordering; not an email approval hash."""
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def model_key(reference: ModelRef) -> str:
    return f"{reference.provider.value}:{reference.model}"


class RegistryStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load(self, revision: int | None = None) -> RegistrySnapshot | None:
        if revision is None:
            revision = await self.session.scalar(
                select(AIRegistryState.revision).where(AIRegistryState.id == 1)
            )
        if not revision:
            return None
        row = await self.session.get(AIRegistryRevision, revision, populate_existing=True)
        if row is None or digest(row.snapshot) != row.digest:
            raise RegistryConflict("Registry revision is missing or corrupted")
        snapshot = RegistrySnapshot.model_validate_json(row.snapshot)
        if snapshot.revision != revision or canonical(snapshot) != row.snapshot:
            raise RegistryConflict("Registry revision binding failed")
        return snapshot

    async def publish(self, proposed: RegistrySnapshot, *, expected_revision: int) -> str:
        # Revalidate at the persistence boundary, including model_construct/copy callers.
        proposed = RegistrySnapshot.model_validate(proposed)
        for schema in proposed.schemas:
            compile_schema(schema.document)
        if expected_revision < 0 or proposed.revision != expected_revision + 1:
            raise RegistryConflict("Registry publication must advance exactly one revision")
        serialized = canonical(proposed)
        from navox.ai.context import reject_credentials

        reject_credentials(serialized)
        if len(serialized.encode("utf-8")) > 8_000_000:
            raise RegistryConflict("Registry snapshot exceeds size limit")
        # SQLite's legacy driver may otherwise release the first SAVEPOINT as a
        # commit. Establish a write transaction so caller rollback stays effective.
        if self.session.get_bind().dialect.name == "sqlite":
            await self.session.execute(
                update(AIRegistryState)
                .where(AIRegistryState.id == 1)
                .values(revision=AIRegistryState.revision)
            )
        async with self.session.begin_nested():
            dialect = self.session.get_bind().dialect.name
            insert = pg_insert if dialect == "postgresql" else sqlite_insert
            if dialect not in {"postgresql", "sqlite"}:
                raise RegistryConflict("Unsupported registry database")
            await self.session.execute(
                insert(AIRegistryState)
                .values(id=1, revision=0)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            current = await self.session.scalar(
                select(AIRegistryState.revision).where(AIRegistryState.id == 1).with_for_update()
            )
            if current != expected_revision:
                raise RegistryConflict("Registry changed; reload before publishing")
            previous = await self.load(current)
            if previous is not None:
                validate_registry_update(previous, proposed)
            changed = await self.session.scalar(
                update(AIRegistryState)
                .where(AIRegistryState.id == 1, AIRegistryState.revision == expected_revision)
                .values(revision=proposed.revision)
                .returning(AIRegistryState.revision)
            )
            if changed is None:
                raise RegistryConflict("Registry changed; reload before publishing")
            self.session.add(
                AIRegistryRevision(
                    revision=proposed.revision, snapshot=serialized, digest=digest(serialized)
                )
            )
            await self._project(proposed)
            await self.session.flush()
        return digest(serialized)

    async def _project(self, proposed: RegistrySnapshot) -> None:
        # Retire removed models, but retain health/evaluation history for auditability.
        await self.session.execute(update(AIModel).values(enabled=False))
        await self.session.execute(update(AIProvider).values(enabled=False))
        for provider in {item.reference.provider for item in proposed.models}:
            provider_row = await self.session.get(AIProvider, provider.value)
            if provider_row is None:
                provider_row = AIProvider(id=provider.value)
                self.session.add(provider_row)
            provider_row.enabled = any(
                m.enabled for m in proposed.models if m.reference.provider == provider
            )
        await self.session.flush()
        await self.session.execute(delete(AIModelCapability))
        for model in proposed.models:
            key = model_key(model.reference)
            serialized = canonical(model)
            row = await self.session.get(AIModel, key)
            if row is None:
                row = AIModel(id=key, provider=model.reference.provider.value)
                self.session.add(row)
            row.definition, row.digest, row.enabled = serialized, digest(serialized), model.enabled
            await self.session.flush()
            for capability in model.capabilities:
                self.session.add(AIModelCapability(model_id=key, capability=capability.value))
            if await self.session.get(AIProviderHealth, key) is None:
                self.session.add(AIProviderHealth(model_id=key))
        assignments = {
            (p.profile.value, model_key(m)) for p in proposed.profiles for m in p.assignments
        }
        for row_assignment in (await self.session.scalars(select(AIProfileAssignment))).all():
            if (row_assignment.profile, row_assignment.model_id) not in assignments:
                await self.session.delete(row_assignment)
        for profile in proposed.profiles:
            profile_row = await self.session.get(AIProfile, profile.profile.value)
            if profile_row is None:
                profile_row = AIProfile(id=profile.profile.value)
                self.session.add(profile_row)
            profile_row.definition = canonical(profile)
            await self.session.flush()
            for reference in profile.assignments:
                assignment_key = (profile.profile.value, model_key(reference))
                if await self.session.get(AIProfileAssignment, assignment_key) is None:
                    self.session.add(
                        AIProfileAssignment(profile=assignment_key[0], model_id=assignment_key[1])
                    )
        for prompt in proposed.prompts:
            if (
                await self.session.get(AIPrompt, (prompt.reference.name, prompt.reference.version))
                is None
            ):
                content = canonical(prompt)
                self.session.add(
                    AIPrompt(
                        name=prompt.reference.name,
                        version=prompt.reference.version,
                        definition=content,
                        digest=digest(content),
                    )
                )
        for schema in proposed.schemas:
            if (
                await self.session.get(AISchema, (schema.reference.name, schema.reference.version))
                is None
            ):
                content = canonical(schema)
                self.session.add(
                    AISchema(
                        name=schema.reference.name,
                        version=schema.reference.version,
                        definition=content,
                        digest=digest(content),
                    )
                )
