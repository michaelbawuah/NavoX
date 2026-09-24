"""Exercise cleanup claims and selected writes against real PostgreSQL locks."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.api import gmail_recheck as api
from navox.api.auth import CurrentAccount
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    Commitment,
    CommitmentSource,
    Connection,
    GmailRecheck,
    ObservationEvidence,
    OperationalObservation,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import (
    ModelExtractionResponse,
    OperationalExtraction,
    source_document_hash,
)
from navox.providers.google_sources import GMAIL_READ_SCOPE


async def verify_gmail_recheck_concurrency(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    user_id, workspace_id, connection_id = uuid4(), uuid4(), uuid4()
    card_ids = [uuid4(), uuid4()]
    observation_id = uuid4()
    now = datetime.now(UTC)
    document = SourceDocument(
        id=uuid4(),
        workspace_id=workspace_id,
        provider="google",
        source_type="gmail_message",
        external_id="cleanup-fixture",
        subject="Optional webinar",
        content="Join if interested.",
        occurred_at=now,
        retrieved_at=now,
    )
    source_hash = source_document_hash(document)
    started, release = asyncio.Event(), asyncio.Event()
    tasks: list[asyncio.Task[api.RecheckItem]] = []

    async def model(*args: object, **kwargs: object) -> ModelExtractionResponse:
        started.set()
        await release.wait()
        return ModelExtractionResponse(
            output=OperationalExtraction().model_dump(), provider="fixture", model="fixture"
        )

    gateway = SimpleNamespace(extract_operational=AsyncMock(side_effect=model))
    read = AsyncMock(return_value=document)

    async def account(database: AsyncSession) -> CurrentAccount:
        user, workspace = (
            await database.get(User, user_id),
            await database.get(Workspace, workspace_id),
        )
        assert user is not None and workspace is not None
        return CurrentAccount(user, workspace)

    async def preview(index: int) -> api.RecheckItem:
        async with sessions() as database:
            return await api.preview_older_item(
                api.PreviewRequest(connection_id=connection_id, commitment_id=card_ids[index]),
                await account(database),
                database,
                Response(),
                settings,
            )

    async def apply(result: api.RecheckItem) -> api.ApplyResponse:
        assert result.preview_id is not None
        async with sessions() as database:
            return await api.apply_selected_previews(
                api.ApplyRequest(
                    connection_id=connection_id,
                    action="remove",
                    items=[
                        api.SelectedPreview(
                            commitment_id=result.commitment_id, preview_id=result.preview_id
                        ),
                    ],
                ),
                await account(database),
                database,
                Response(),
            )

    try:
        async with sessions() as database:
            database.add_all(
                [
                    User(id=user_id, email=f"cleanup-ci-{user_id}@example.test"),
                    Workspace(id=workspace_id, name="Isolated Gmail cleanup fixture"),
                ]
            )
            await database.flush()
            database.add_all(
                [
                    WorkspaceMembership(workspace_id=workspace_id, user_id=user_id),
                    Connection(
                        id=connection_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        provider="google",
                        external_account_id=str(connection_id),
                        external_email="owner@example.test",
                        granted_scopes=[GMAIL_READ_SCOPE],
                    ),
                    OperationalObservation(
                        id=observation_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        observation_type="request",
                        confidence=Decimal("0.980"),
                        extractor_version="operational-extraction.v1",
                    ),
                ]
            )
            await database.flush()
            database.add(
                ObservationEvidence(
                    observation_id=observation_id,
                    connection_id=connection_id,
                    provider="google",
                    source_type="gmail_message",
                    external_resource_id=document.external_id,
                    source_hash=source_hash,
                    observed_at=now,
                    evidence_locator={
                        "spans": [{"source": "subject", "start_char": 0, "end_char": 16}]
                    },
                )
            )
            for card_id in card_ids:
                database.add(
                    Commitment(
                        id=card_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        commitment_type="task",
                        title="Attend optional webinar",
                        status="confirmed",
                        created_by="ai",
                        dedupe_key=str(card_id),
                    )
                )
                await database.flush()
                database.add(
                    CommitmentSource(
                        commitment_id=card_id,
                        connection_id=connection_id,
                        provider="google",
                        source_type="gmail_message",
                        external_resource_id=document.external_id,
                        source_metadata={
                            "observation_id": str(observation_id),
                            "source_hash": source_hash,
                        },
                    )
                )
            await database.commit()
        with (
            patch.object(api, "build_ai_gateway", return_value=gateway),
            patch.object(
                api, "access_token_for_connection", AsyncMock(return_value="synthetic-token")
            ),
            patch.object(
                api, "GoogleSourceGateway", return_value=SimpleNamespace(gmail_message=read)
            ),
        ):
            first = asyncio.create_task(preview(0))
            tasks.append(first)
            await asyncio.wait_for(started.wait(), timeout=10)
            repeat = await asyncio.wait_for(preview(0), timeout=10)
            assert repeat.outcome == "checking"
            try:
                await asyncio.wait_for(preview(1), timeout=10)
            except HTTPException as error:
                assert error.status_code == 409
            else:
                raise AssertionError("Concurrent recheck must not start another model request")
            gateway.extract_operational.assert_awaited_once()
            read.assert_awaited_once()
            release.set()
            result = await asyncio.wait_for(first, timeout=10)
            assert result.outcome == "remove_suggested"
            applied = await asyncio.wait_for(
                asyncio.gather(*(apply(result) for _ in range(4))), timeout=20
            )
            assert all(row.applied == [card_ids[0]] and not row.skipped for row in applied)
            async with sessions() as database:
                assert (
                    await database.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(
                            AuditEvent.workspace_id == workspace_id,
                            AuditEvent.event_type == "commitment.dismissed",
                        )
                    )
                    == 1
                )
                card = await database.get(Commitment, card_ids[1])
                assert card is not None and card.status == "confirmed"

            # An owner edit can commit during model I/O; the old preview must lose authority.
            started.clear()
            release.clear()
            second = asyncio.create_task(preview(1))
            tasks.append(second)
            await asyncio.wait_for(started.wait(), timeout=10)
            async with sessions() as database:
                card = await database.get(Commitment, card_ids[1])
                assert card is not None
                card.status = "completed"
                await database.commit()
            release.set()
            changed = await asyncio.wait_for(second, timeout=10)
            assert changed.outcome == "needs_review" and changed.reason == "item_changed"
            assert (await apply(changed)).skipped == [card_ids[1]]
            async with sessions() as database:
                saved = await database.get(GmailRecheck, card_ids[1])
                assert saved is not None and saved.outcome == "needs_review"
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with sessions() as database:
            await database.execute(delete(Workspace).where(Workspace.id == workspace_id))
            await database.execute(delete(User).where(User.id == user_id))
            await database.commit()
