"""Export and apply an explicitly reviewed, fresh historical provenance manifest.

This repairs source links only. It never disconnects, deletes data, or executes
an action. Provider-based identity suggestions are proposals, not evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.lifecycle import _locked_ownership
from navox.db.models import (
    Action,
    Approval,
    AuditEvent,
    Commitment,
    CommitmentSource,
    Connection,
    Person,
    PersonIdentity,
    PersonIdentitySource,
    Plan,
    PlanSource,
    PlanStep,
    WorkspaceMembership,
)


class HistoricalReviewError(ValueError):
    """Content-free operator diagnostic."""


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["identity", "plan"]
    id: UUID
    # None is unresolved. Empty is permitted only for reviewed independent plans.
    connection_ids: list[UUID] | None


class ReviewManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["spec-003-historical-provenance.v1"] = (
        "spec-003-historical-provenance.v1"
    )
    connection_id: UUID
    workspace_id: UUID
    user_id: UUID
    snapshot: dict[str, object]
    assignments: list[Assignment]


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return value


def _record(row: object) -> dict[str, object]:
    mapper = inspect(type(row))
    if mapper is None:
        raise HistoricalReviewError("invalid_review_record")
    return {prop.key: _json_value(getattr(row, prop.key)) for prop in mapper.column_attrs}


async def _snapshot(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, provider: str
) -> dict[str, object]:
    identities = list(
        await database.scalars(
            select(PersonIdentity)
            .where(
                PersonIdentity.workspace_id == workspace_id,
                PersonIdentity.source_attributed.is_(False),
                PersonIdentity.provider == provider,
            )
            .order_by(PersonIdentity.id)
        )
    )
    if (
        identities
        and await database.scalar(
            select(func.count())
            .select_from(WorkspaceMembership)
            .where(WorkspaceMembership.workspace_id == workspace_id)
        )
        != 1
    ):
        raise HistoricalReviewError("shared_workspace_identity_history_requires_separate_review")
    plans = list(
        await database.scalars(
            select(Plan)
            .where(
                Plan.workspace_id == workspace_id,
                Plan.user_id == user_id,
                Plan.source_attributed.is_(False),
            )
            .order_by(Plan.id)
        )
    )
    steps = list(
        await database.scalars(
            select(PlanStep)
            .where(PlanStep.plan_id.in_([p.id for p in plans]))
            .order_by(PlanStep.id)
        )
    )
    actions = list(
        await database.scalars(
            select(Action).where(Action.plan_step_id.in_([s.id for s in steps])).order_by(Action.id)
        )
    )
    approvals = list(
        await database.scalars(
            select(Approval)
            .where(Approval.action_id.in_([a.id for a in actions]))
            .order_by(Approval.id)
        )
    )
    if any((row.workspace_id, row.user_id) != (workspace_id, user_id) for row in actions) or any(
        (row.workspace_id, row.user_id) != (workspace_id, user_id) for row in approvals
    ):
        raise HistoricalReviewError("artifact_ownership_mismatch")
    connections = list(
        await database.scalars(
            select(Connection)
            .where(Connection.workspace_id == workspace_id, Connection.user_id == user_id)
            .order_by(Connection.id)
        )
    )
    identity_links = list(
        await database.scalars(
            select(PersonIdentitySource)
            .where(PersonIdentitySource.identity_id.in_([i.id for i in identities]))
            .order_by(PersonIdentitySource.identity_id, PersonIdentitySource.connection_id)
        )
    )
    plan_links = list(
        await database.scalars(
            select(PlanSource)
            .where(PlanSource.plan_id.in_([p.id for p in plans]))
            .order_by(PlanSource.plan_id, PlanSource.connection_id)
        )
    )
    # Include root and related cards. Unrelated live ingestion must not invalidate
    # an otherwise unchanged historical review.
    commitment_ids = {p.commitment_id for p in plans if p.commitment_id is not None}
    for plan in plans:
        relations = plan.context_snapshot.get("relations", [])
        if isinstance(relations, list):
            for relation in relations:
                if isinstance(relation, dict) and isinstance(relation.get("commitment_id"), str):
                    try:
                        commitment_ids.add(UUID(relation["commitment_id"]))
                    except ValueError:
                        raise HistoricalReviewError("invalid_historical_relation") from None
    sources = list(
        await database.scalars(
            select(CommitmentSource)
            .join(Commitment, Commitment.id == CommitmentSource.commitment_id)
            .where(
                Commitment.workspace_id == workspace_id,
                Commitment.user_id == user_id,
                Commitment.id.in_(commitment_ids),
            )
            .order_by(CommitmentSource.id)
        )
    )
    people = list(
        await database.scalars(
            select(Person)
            .where(Person.id.in_([i.person_id for i in identities]))
            .order_by(Person.id)
        )
    )
    if {p.id for p in people} != {i.person_id for i in identities} or any(
        p.workspace_id != workspace_id for p in people
    ):
        raise HistoricalReviewError("identity_person_ownership_mismatch")
    return {
        "identities": [_record(i) for i in identities],
        "people": [_record(p) for p in people],
        "identity_links": [_record(s) for s in identity_links],
        "plans": [_record(p) for p in plans],
        "steps": [_record(s) for s in steps],
        "actions": [_record(a) for a in actions],
        "approvals": [_record(a) for a in approvals],
        "plan_links": [_record(s) for s in plan_links],
        "commitment_sources": [_record(s) for s in sources],
        # Never export credentials, tokens, account emails or connection config.
        "connections": [{"id": str(c.id), "provider": c.provider} for c in connections],
    }


def _records(snapshot: dict[str, object], key: str) -> list[dict[str, object]]:
    value = snapshot[key]
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise HistoricalReviewError("invalid_snapshot")
    return value


def _propose(snapshot: dict[str, object]) -> list[Assignment]:
    connections = _records(snapshot, "connections")
    proposals: list[Assignment] = []
    for identity in _records(snapshot, "identities"):
        candidates = [
            UUID(str(c["id"])) for c in connections if c["provider"] == identity["provider"]
        ]
        proposals.append(
            Assignment(
                kind="identity",
                id=UUID(str(identity["id"])),
                connection_ids=candidates if len(candidates) == 1 else None,
            )
        )
    for plan in _records(snapshot, "plans"):
        context = plan["context_snapshot"]
        if not isinstance(context, dict):
            raise HistoricalReviewError("invalid_plan_snapshot")
        roots = {str(plan["commitment_id"])}
        relations = context.get("relations", [])
        if isinstance(relations, list):
            roots.update(str(r.get("commitment_id")) for r in relations if isinstance(r, dict))
        ids = {
            UUID(str(s["connection_id"]))
            for s in _records(snapshot, "commitment_sources")
            if str(s["commitment_id"]) in roots and s["connection_id"] is not None
        }
        explicit = context.get("connection")
        if isinstance(explicit, dict) and explicit.get("id"):
            ids.add(UUID(str(explicit["id"])))
        ids.update(
            UUID(str(s["connection_id"]))
            for s in _records(snapshot, "plan_links")
            if s["plan_id"] == plan["id"]
        )
        # An empty or unrecognized snapshot cannot propose independent origin.
        known = isinstance(context.get("commitment"), dict) and (
            isinstance(context.get("sources"), list) or isinstance(explicit, dict)
        )
        proposals.append(
            Assignment(
                kind="plan",
                id=UUID(str(plan["id"])),
                connection_ids=sorted(ids, key=str) if known else None,
            )
        )
    return proposals


async def export_review(
    database: AsyncSession, *, connection_id: UUID, workspace_id: UUID, user_id: UUID
) -> ReviewManifest:
    rows, account = await _locked_ownership(database, workspace_id, user_id, connection_id)
    provider = account.provider if account else rows[0].provider
    snapshot = await _snapshot(
        database, workspace_id=workspace_id, user_id=user_id, provider=provider
    )
    manifest = ReviewManifest(
        connection_id=connection_id,
        workspace_id=workspace_id,
        user_id=user_id,
        snapshot=snapshot,
        assignments=_propose(snapshot),
    )
    await database.rollback()  # Export cannot persist changes.
    return manifest


async def apply_review(
    database: AsyncSession, manifest: ReviewManifest, *, approved_sha256: str
) -> dict[str, object]:
    try:
        return await _apply_review(database, manifest, approved_sha256=approved_sha256)
    except Exception:
        await database.rollback()
        raise


async def _apply_review(
    database: AsyncSession, manifest: ReviewManifest, *, approved_sha256: str
) -> dict[str, object]:
    approved = digest(manifest.model_dump(mode="json"))
    if approved != approved_sha256:
        raise HistoricalReviewError("review_digest_mismatch")
    rows, account = await _locked_ownership(
        database, manifest.workspace_id, manifest.user_id, manifest.connection_id
    )
    receipt = await database.scalar(
        select(AuditEvent.id).where(
            AuditEvent.workspace_id == manifest.workspace_id,
            AuditEvent.user_id == manifest.user_id,
            AuditEvent.entity_id == manifest.connection_id,
            AuditEvent.event_type == "connector.management.provenance_review",
            AuditEvent.event_metadata["review_sha256"].as_string() == approved,
        )
    )
    if receipt:
        await database.rollback()
        return {"applied": False, "already_applied": True, "deleted_records": 0}
    provider = account.provider if account else rows[0].provider
    snapshot = await _snapshot(
        database, workspace_id=manifest.workspace_id, user_id=manifest.user_id, provider=provider
    )
    if digest(snapshot) != digest(manifest.snapshot):
        raise HistoricalReviewError("history_changed_export_a_fresh_review")
    expected = {
        (kind, UUID(str(row["id"])))
        for kind, key in (("identity", "identities"), ("plan", "plans"))
        for row in _records(snapshot, key)
    }
    keys = [(a.kind, a.id) for a in manifest.assignments]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise HistoricalReviewError("review_must_cover_each_record_exactly_once")
    allowed = {UUID(str(c["id"])) for c in _records(snapshot, "connections")}
    for assignment in manifest.assignments:
        ids = assignment.connection_ids
        if ids is None or len(ids) != len(set(ids)) or (assignment.kind == "identity" and not ids):
            raise HistoricalReviewError("unresolved_source_assignment")
        if not set(ids).issubset(allowed):
            raise HistoricalReviewError("source_connection_ownership_mismatch")
        link_key, id_key = (
            ("identity_links", "identity_id")
            if assignment.kind == "identity"
            else ("plan_links", "plan_id")
        )
        known = {
            UUID(str(s["connection_id"]))
            for s in _records(snapshot, link_key)
            if s[id_key] == str(assignment.id)
        }
        if not known.issubset(ids):
            raise HistoricalReviewError("review_cannot_remove_known_source_links")
        if assignment.kind == "identity":
            identity = await database.get(PersonIdentity, assignment.id)
            if identity is None:
                raise HistoricalReviewError("history_changed_export_a_fresh_review")
            identity.source_attributed = True
            for identifier in set(ids) - known:
                database.add(
                    PersonIdentitySource(identity_id=identity.id, connection_id=identifier)
                )
        else:
            plan = await database.get(Plan, assignment.id)
            if plan is None:
                raise HistoricalReviewError("history_changed_export_a_fresh_review")
            explicit = plan.context_snapshot.get("connection")
            if (
                isinstance(explicit, dict)
                and explicit.get("id")
                and UUID(str(explicit["id"])) not in ids
            ):
                raise HistoricalReviewError("review_cannot_remove_explicit_plan_connection")
            plan.source_attributed = True
            for identifier in set(ids) - known:
                database.add(PlanSource(plan_id=plan.id, connection_id=identifier))
    database.add(
        AuditEvent(
            workspace_id=manifest.workspace_id,
            user_id=manifest.user_id,
            actor_type="user",
            actor_id=str(manifest.user_id),
            entity_type="connection",
            entity_id=manifest.connection_id,
            event_type="connector.management.provenance_review",
            event_metadata={"review_sha256": approved, "records": len(keys)},
        )
    )
    await database.commit()
    return {"applied": True, "reviewed_records": len(keys), "deleted_records": 0}
