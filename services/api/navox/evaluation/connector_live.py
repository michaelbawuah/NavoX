"""Run mandatory SPEC-003 demonstration D using an approved live connection.

This operator command uses the deployed Temporal worker and its real adapter/model
configuration. It never creates credentials, grants capabilities, or substitutes
provider/model fixtures. Reports contain identifiers and counts, not source text.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.catalog import build_connector_registry
from navox.connectors.dispatcher import dispatch_connector_sync
from navox.connectors.jobs import ConnectorSyncWork
from navox.core.settings import Settings, get_settings
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSyncReceipt,
    ConnectorSyncRun,
    User,
)
from navox.db.session import get_session_factory
from navox.intelligence.extraction import OPERATIONAL_EXTRACTION_SCHEMA_VERSION
from navox.today.projection import build_today_projection


class LiveAcceptanceError(ValueError):
    """Fixed diagnostics only; provider and database exceptions are not printed."""


async def approved_target(
    database: AsyncSession, connection_id: UUID, settings: Settings
) -> tuple[ConnectorConnection, ConnectorDefinition]:
    connection = await database.get(ConnectorConnection, connection_id)
    if connection is None:
        raise LiveAcceptanceError("connection_not_found")
    try:
        connection = await owned_connector(
            database,
            connection_id=connection.id,
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            require_active=True,
        )
    except ConnectorAccessDenied:
        raise LiveAcceptanceError("connection_not_authorized") from None
    definition = await database.get(ConnectorDefinition, connection.connector_definition_id)
    if (
        definition is None
        or definition.connector_class not in {"GENERIC_API", "MCP"}
        or connection.provider in {"google", "canvas", "import"}
    ):
        raise LiveAcceptanceError("approved_rest_or_mcp_connection_required")
    try:
        manifest = build_connector_registry(settings).get(definition.connector_key).manifest
    except (KeyError, ValueError):
        raise LiveAcceptanceError("connector_not_in_current_operator_approval") from None
    if definition.version != manifest.version or definition.manifest != manifest.model_dump(
        mode="json", by_alias=True
    ):
        raise LiveAcceptanceError("connector_manifest_changed")
    return connection, definition


async def inspect_run(
    database: AsyncSession,
    *,
    connection_id: UUID,
    request_id: UUID,
    settings: Settings,
) -> dict[str, object]:
    connection, definition = await approved_target(database, connection_id, settings)
    run = await database.scalar(
        select(ConnectorSyncRun).where(
            ConnectorSyncRun.connector_connection_id == connection.id,
            ConnectorSyncRun.workspace_id == connection.workspace_id,
            ConnectorSyncRun.request_id == request_id,
        )
    )
    if run is None:
        raise LiveAcceptanceError("sync_run_not_recorded")
    receipts = list(
        await database.scalars(
            select(ConnectorSyncReceipt)
            .join(ConnectorResource, ConnectorResource.id == ConnectorSyncReceipt.resource_id)
            .where(
                ConnectorSyncReceipt.sync_run_id == run.id,
                ConnectorSyncReceipt.workspace_id == connection.workspace_id,
                ConnectorSyncReceipt.consumer_version == OPERATIONAL_EXTRACTION_SCHEMA_VERSION,
                ConnectorSyncReceipt.outcome.in_(("processed", "duplicate")),
                ConnectorResource.connector_connection_id == connection.id,
                ConnectorResource.workspace_id == connection.workspace_id,
            )
            .limit(10_001)
        )
    )
    result_ids = {identifier for receipt in receipts for identifier in receipt.result_ids}
    owner = await database.get(User, connection.user_id)
    assert owner is not None  # approved_target checked membership and owner.
    projection = await build_today_projection(
        database,
        user_id=connection.user_id,
        workspace_id=connection.workspace_id,
        timezone_name=owner.timezone,
        now=datetime.now(UTC),
    )
    items = (
        *projection.needs_attention,
        *projection.coming_up,
        *projection.renewals,
        *projection.waiting_on,
        *projection.completed_recently,
        *projection.set_aside,
    )
    matches = {
        item.id
        for item in items
        if str(item.id) in result_ids
        and any(
            source.connection_id == connection.legacy_connection_id
            and source.provider == connection.provider
            and source.evidence_id is not None
            for source in item.sources
        )
    }
    checks = {
        "sync_completed": run.status == "completed" and run.fetch_complete,
        "resources_seen": run.resource_count > 0,
        "canonical_acceptance_recorded": 0 < len(receipts) <= 10_000,
        "spec_002_results_recorded": bool(result_ids),
        "source_linked_results_in_today": bool(matches),
    }
    return {
        "schema_version": "spec-003-live-portability.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "connection_id": str(connection.id),
        "request_id": str(request_id),
        "sync_run_id": str(run.id),
        "connector_class": definition.connector_class,
        "manifest_sha256": hashlib.sha256(
            json.dumps(definition.manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "resources_seen": run.resource_count,
        "processed_revisions": run.processed_count,
        "duplicate_revisions": run.duplicate_count,
        "accepted_receipts": len(receipts),
        "spec_002_result_count": len(result_ids),
        "today_matches": len(matches),
        "checks": checks,
        "passed": all(checks.values()),
    }


async def run_live_acceptance(
    connection_id: UUID, *, request_id: UUID, timeout_seconds: int = 300
) -> dict[str, object]:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as database:
        connection, _ = await approved_target(database, connection_id, settings)
        payload = ConnectorSyncWork(
            connection_id=str(connection.id),
            user_id=str(connection.user_id),
            workspace_id=str(connection.workspace_id),
            request_id=str(request_id),
            trigger="manual",
        )
        # Never accept old receipts as evidence of this command's provider call.
        existing = await database.scalar(
            select(ConnectorSyncRun.id).where(
                ConnectorSyncRun.connector_connection_id == connection.id,
                ConnectorSyncRun.request_id == request_id,
            )
        )
        if existing is not None:
            raise LiveAcceptanceError("fresh_request_id_required")
        await database.commit()
    workflow_id = await dispatch_connector_sync(payload, settings=settings)
    client = await Client.connect(settings.temporal_target)
    try:
        await asyncio.wait_for(
            client.get_workflow_handle(workflow_id).result(), timeout=timeout_seconds
        )
    except TimeoutError:
        # Cancel waiting only. The durable sync continues under normal recovery.
        raise LiveAcceptanceError("wait_timed_out_sync_may_still_be_running") from None
    async with factory() as database:
        report = await inspect_run(
            database, connection_id=connection_id, request_id=request_id, settings=settings
        )
    report["workflow_id"] = workflow_id
    report["execution"] = "deployed_temporal_worker"
    return report


async def list_candidates() -> list[dict[str, str]]:
    async with get_session_factory()() as database:
        rows = await database.execute(
            select(
                ConnectorConnection.id,
                ConnectorDefinition.connector_class,
                ConnectorConnection.status,
            )
            .join(
                ConnectorDefinition,
                ConnectorDefinition.id == ConnectorConnection.connector_definition_id,
            )
            .where(ConnectorDefinition.connector_class.in_(("GENERIC_API", "MCP")))
            .order_by(ConnectorConnection.id)
            .limit(100)
        )
        return [
            {"connection_id": str(identifier), "connector_class": kind, "status": status}
            for identifier, kind, status in rows
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--list", action="store_true", help="List REST/MCP connection IDs without secrets"
    )
    mode.add_argument("--connection-id", type=UUID)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Start a real sync using the configured worker and AI provider",
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=300, choices=range(30, 1801), metavar="30..1800"
    )
    arguments = parser.parse_args()
    if not arguments.list and not arguments.live:
        parser.error("--live is required to start provider/model I/O")
    request_id = uuid4()
    try:
        report = asyncio.run(
            list_candidates()
            if arguments.list
            else run_live_acceptance(
                arguments.connection_id,
                request_id=request_id,
                timeout_seconds=arguments.timeout_seconds,
            )
        )
    except LiveAcceptanceError as error:
        print(json.dumps({"passed": False, "error": str(error), "request_id": str(request_id)}))
        return 1
    except Exception:
        # Do not expose DB credentials, provider bodies, tokens or stack traces.
        print(
            json.dumps(
                {"passed": False, "error": "live_run_unavailable", "request_id": str(request_id)}
            )
        )
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if arguments.list or (isinstance(report, dict) and report["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
