"""Read-only inventory of historical data that can block connection deletion.

This operator report never assigns provenance, retires watches, or deletes data.
Identifiers and counts are sufficient for review; identity values and source text
are deliberately omitted. The deletion transaction remains the final authority.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    Action,
    Approval,
    CommitmentSource,
    Connection,
    ConnectorConnection,
    ConnectorResource,
    ConnectorSubscription,
    ConnectorSyncRun,
    ObservationEvidence,
    OperationalObservation,
    PersonIdentity,
    Plan,
    ProviderEventSubscription,
    WorkspaceMembership,
)
from navox.db.session import get_session_factory


class DeletionReviewError(ValueError):
    """Fixed diagnostics without database or source contents."""


async def inspect_connection(database: AsyncSession, connection_id: UUID) -> dict[str, object]:
    account = await database.get(Connection, connection_id)
    native = await database.get(ConnectorConnection, connection_id)
    if account is not None and account.provider != "google":
        account = None
    owner = account or native
    if (
        owner is None
        or await database.get(WorkspaceMembership, (owner.workspace_id, owner.user_id)) is None
    ):
        raise DeletionReviewError("connection_not_found_or_owner_unavailable")
    workspace_id, user_id = owner.workspace_id, owner.user_id
    rows = list(
        await database.scalars(
            select(ConnectorConnection).where(
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.user_id == user_id,
                (ConnectorConnection.id == connection_id)
                | (ConnectorConnection.legacy_connection_id == connection_id),
            )
        )
    )
    native_ids = [row.id for row in rows]
    provenance_ids = (
        {account.id}
        if account
        else {row.legacy_connection_id for row in rows if row.legacy_connection_id}
    )
    providers = {row.provider for row in rows} | ({account.provider} if account else set())
    counts: dict[str, int] = {}
    watches = list(
        await database.scalars(
            select(ProviderEventSubscription).where(
                ProviderEventSubscription.connection_id.in_(provenance_ids),
                ProviderEventSubscription.status != "cancelled",
            )
        )
    )
    counts["pending_provider_watches"] = len(watches)
    counts["unconfirmed_provider_watches"] = sum(
        watch.expiration_confirmed_at is None for watch in watches
    )
    counts["pending_connector_subscriptions"] = int(
        await database.scalar(
            select(func.count())
            .select_from(ConnectorSubscription)
            .where(
                ConnectorSubscription.connector_connection_id.in_(native_ids),
                ConnectorSubscription.status != "cancelled",
            )
        )
        or 0
    )
    people: set[UUID] = set()
    for subject, object_ in await database.execute(
        select(OperationalObservation.subject_person_id, OperationalObservation.object_person_id)
        .join(ObservationEvidence, ObservationEvidence.observation_id == OperationalObservation.id)
        .where(
            ObservationEvidence.connection_id.in_(provenance_ids),
            OperationalObservation.workspace_id == workspace_id,
            OperationalObservation.user_id == user_id,
        )
    ):
        people.update(identifier for identifier in (subject, object_) if identifier)
    counts["unattributed_person_identities"] = int(
        await database.scalar(
            select(func.count())
            .select_from(PersonIdentity)
            .where(
                PersonIdentity.workspace_id == workspace_id,
                PersonIdentity.source_attributed.is_(False),
                (PersonIdentity.provider.in_(providers)) | (PersonIdentity.person_id.in_(people)),
            )
        )
        or 0
    )
    affected = int(
        await database.scalar(
            select(func.count(func.distinct(CommitmentSource.commitment_id))).where(
                CommitmentSource.connection_id.in_(provenance_ids)
            )
        )
        or 0
    )
    for label, model in (("plans", Plan), ("actions", Action), ("approvals", Approval)):
        counts[f"potentially_embedded_{label}"] = (
            int(
                await database.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.workspace_id == workspace_id, model.user_id == user_id)
                )
                or 0
            )
            if affected
            else 0
        )
    counts["shared_provenance_anchors"] = int(
        await database.scalar(
            select(func.count())
            .select_from(ConnectorConnection)
            .where(
                ConnectorConnection.legacy_connection_id.in_(provenance_ids),
                ConnectorConnection.id.notin_(native_ids),
            )
        )
        or 0
    )
    counts["ingestion_without_provenance_anchor"] = 0
    counts["unattributed_plans"] = int(
        await database.scalar(
            select(func.count())
            .select_from(Plan)
            .where(
                Plan.workspace_id == workspace_id,
                Plan.user_id == user_id,
                Plan.source_attributed.is_(False),
            )
        )
        or 0
    )
    if not provenance_ids:
        for ingested_model in (ConnectorResource, ConnectorSyncRun):
            counts["ingestion_without_provenance_anchor"] += int(
                await database.scalar(
                    select(func.count())
                    .select_from(ingested_model)
                    .where(ingested_model.connector_connection_id.in_(native_ids))
                )
                or 0
            )
    return {
        "schema_version": "spec-003-deletion-review.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "connection_id": str(connection_id),
        "workspace_id": str(workspace_id),
        "disconnected": (account is None or account.status == "disconnected")
        and all(row.status == "DISCONNECTED" for row in rows),
        "affected_commitments": affected,
        "review_counts": counts,
        "requires_historical_review": any(
            value for key, value in counts.items() if not key.startswith("potentially_embedded_")
        ),
        "deletion_authorized": False,
        "limitations": (
            "Read-only inventory; deletion also validates surviving and "
            "cross-workspace provenance transactionally."
        ),
    }


async def run(connection_id: UUID | None) -> object:
    async with get_session_factory()() as database:
        if connection_id is not None:
            return await inspect_connection(database, connection_id)
        legacy = await database.scalars(
            select(Connection.id)
            .where(Connection.provider == "google")
            .order_by(Connection.id)
            .limit(101)
        )
        native = await database.scalars(
            select(ConnectorConnection.id)
            .where(ConnectorConnection.provider != "google")
            .order_by(ConnectorConnection.id)
            .limit(101)
        )
        identifiers = sorted({*legacy, *native}, key=str)
        return {
            "connection_ids": [str(value) for value in identifiers[:100]],
            "truncated": len(identifiers) > 100,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--connection-id", type=UUID)
    mode.add_argument("--list", action="store_true")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.connection_id))
    except DeletionReviewError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    except Exception:
        print(json.dumps({"error": "deletion_review_unavailable"}))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
