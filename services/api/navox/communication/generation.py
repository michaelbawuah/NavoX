"""Draft from one explicitly selected Gmail source; generation cannot authorize sending."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.context import ContextBuilder
from navox.ai.features import configured_secrets, source_task
from navox.ai.foundation.contracts import AIResult, LatencyClass, Profile, TaskType
from navox.ai.runtime import GatewayRuntime
from navox.approvals.service import ApprovalNotFoundError, ApprovalPermissionError
from navox.communication.schemas import DraftContent
from navox.communication.service import authorize_source
from navox.connectors.builtin.google import (
    ensure_google_connector_connection,
    google_canonical_resource,
)
from navox.core.settings import Settings
from navox.db.communications import CommunicationDraft
from navox.db.models import Commitment, CommitmentSource
from navox.intelligence.extraction import INSTRUCTION_LIKE_MARKERS
from navox.intelligence.ingestion import authorized_connection
from navox.intelligence.source_cooldown import source_retry_after
from navox.providers.google_oauth import access_token_for_connection
from navox.providers.google_sources import GoogleSourceGateway


async def generate_content(
    database: AsyncSession,
    runtime: GatewayRuntime,
    *,
    settings: Settings,
    workspace_id: UUID,
    user_id: UUID,
    commitment_id: UUID,
    source_id: UUID,
    recipient: str,
    instructions: str,
    previous: DraftContent | None = None,
) -> tuple[DraftContent, AIResult, UUID]:
    source = await database.scalar(
        select(CommitmentSource)
        .join(Commitment)
        .where(
            CommitmentSource.id == source_id,
            Commitment.id == commitment_id,
            Commitment.workspace_id == workspace_id,
            Commitment.user_id == user_id,
            CommitmentSource.provider == "google",
            CommitmentSource.source_type == "gmail_message",
        )
    )
    if source is None or source.connection_id is None or not source.external_resource_id:
        raise ApprovalNotFoundError("A readable Gmail source for this commitment is required")
    connection_id, external_id = source.connection_id, source.external_resource_id
    scope = CommunicationDraft(
        id=uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        commitment_id=commitment_id,
        source_connection_id=connection_id,
        source_reference=str(source_id),
    )
    await authorize_source(database, scope)
    async with asyncio.timeout(30):
        connection, _ = await authorized_connection(database, connection_id, "gmail")
        if await source_retry_after(database, connection_id, "gmail"):
            raise ApprovalPermissionError("Gmail is temporarily rate limited")
        token = await access_token_for_connection(
            database, connection=connection, settings=settings
        )
        await database.commit()
        await authorize_source(database, scope)
        document = await GoogleSourceGateway().gmail_message(
            token,
            workspace_id=workspace_id,
            connection_id=connection_id,
            external_id=external_id,
            now=datetime.now(UTC),
        )
    await authorize_source(database, scope)
    if (document.workspace_id, document.provider, document.source_type, document.external_id) != (
        workspace_id,
        "google",
        "gmail_message",
        external_id,
    ) or document.metadata.get("status") == "deleted":
        raise ApprovalPermissionError("Gmail source binding failed")
    connector = await ensure_google_connector_connection(database, connection)
    resource = google_canonical_resource(document, connector_connection_id=connector.id)
    await database.commit()  # Persist only the compatibility connection, never the message body.
    task = source_task(
        workspace_id=workspace_id,
        user_id=user_id,
        connector=connector,
        source_id=resource.resource_id,
        policy=runtime.store.operator_policy,
        profile=Profile.ASSISTANT_INTERACTIVE,
        task_type=TaskType.DRAFT_COMMUNICATION,
        prompt="communication_draft",
        latency=LatencyClass.INTERACTIVE,
        max_output_tokens=2000,
    )
    request: dict[str, object] = {"instructions": instructions}
    if previous is not None:
        request["previous_draft"] = {"subject": previous.subject, "body": previous.body}
        request["edit_rule"] = (
            "Preserve the user's edits unless their new instructions request a change."
        )

    def validate(output: Any) -> None:
        if not isinstance(output, dict):
            raise ValueError("Draft must be an object")
        content = DraftContent(to=[recipient], subject=output["subject"], body=output["body"])
        text = (content.subject + "\n" + content.body).casefold()
        if any(marker in text for marker in INSTRUCTION_LIKE_MARKERS):
            raise ValueError("Draft contains an instruction-like proposal")

    result = await runtime.execute(
        task,
        context_builder=ContextBuilder(
            database, secrets=configured_secrets(settings), fetched_sources=(resource,)
        ),
        documents={resource.resource_id: document},
        semantic_validator=validate,
        user_request=json.dumps(request),
    )
    await authorize_source(database, scope)
    output = json.loads(result.output.text)
    return (
        DraftContent(to=[recipient], subject=output["subject"], body=output["body"]),
        result,
        connection_id,
    )
