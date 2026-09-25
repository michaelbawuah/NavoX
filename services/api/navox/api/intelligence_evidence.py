"""Read bounded evidence only on request, without retaining source bodies."""

import asyncio
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.db.models import (
    Connection,
    ObservationEvidence,
    OperationalObservation,
    User,
    WorkspaceMembership,
)
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import source_document_hash
from navox.intelligence.source_cooldown import source_retry_after
from navox.providers.google_oauth import GoogleAccessTokenError, access_token_for_connection
from navox.providers.google_sources import GMAIL_READ_SCOPE, GoogleSourceError, GoogleSourceGateway

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class EvidenceExcerpt(BaseModel):
    source: Literal["subject", "content"]
    text: str


class EvidenceResponse(BaseModel):
    evidence_id: UUID
    excerpts: list[EvidenceExcerpt]


def unavailable(status: int, detail: str) -> HTTPException:
    return HTTPException(status, detail, headers={"Cache-Control": "no-store"})


async def readable_connection(
    database: AsyncSession, *, connection_id: UUID, user_id: UUID, workspace_id: UUID
) -> Connection:
    connection = await database.scalar(
        select(Connection)
        .join(User, User.id == Connection.user_id)
        .join(
            WorkspaceMembership,
            (WorkspaceMembership.user_id == Connection.user_id)
            & (WorkspaceMembership.workspace_id == Connection.workspace_id),
        )
        .where(
            Connection.id == connection_id,
            Connection.user_id == user_id,
            Connection.workspace_id == workspace_id,
            Connection.provider == "google",
            Connection.status == "active",
            User.agent_paused.is_(False),
        )
        .execution_options(populate_existing=True)
    )
    if connection is None or GMAIL_READ_SCOPE not in connection.granted_scopes:
        raise unavailable(403, "Resume NavoX and enable Gmail read access to view this source.")
    return connection


def verified_excerpts(
    evidence: ObservationEvidence, document: SourceDocument
) -> list[EvidenceExcerpt]:
    hashes = {source_document_hash(document)}
    # Older Gmail evidence predates this boolean metadata field. Removing only
    # that field supports its old hash without accepting any changed source text.
    if document.metadata.get("list_unsubscribe") is True:
        legacy_metadata = dict(document.metadata)
        legacy_metadata.pop("list_unsubscribe")
        hashes.add(source_document_hash(document.model_copy(update={"metadata": legacy_metadata})))
    if evidence.source_hash not in hashes:
        raise unavailable(
            409, "The source changed since this item was extracted. Sync Gmail again."
        )
    spans = (evidence.evidence_locator or {}).get("spans")
    if not isinstance(spans, list) or not 1 <= len(spans) <= 8:
        raise unavailable(409, "The saved source reference cannot be verified.")
    excerpts: list[EvidenceExcerpt] = []
    for span in spans:
        if not isinstance(span, dict):
            raise unavailable(409, "The saved source reference cannot be verified.")
        source, start, end = span.get("source"), span.get("start_char"), span.get("end_char")
        text = document.subject if source == "subject" else document.content
        if (
            source not in ("subject", "content")
            or type(start) is not int
            or type(end) is not int
            or text is None
            or not 0 <= start < end <= len(text)
            or end - start > 512
        ):
            raise unavailable(409, "The saved source reference cannot be verified.")
        excerpts.append(EvidenceExcerpt(source=source, text=text[start:end]))
    return excerpts


@router.get("/evidence/{evidence_id}", response_model=EvidenceResponse)
async def read_evidence(
    evidence_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
    settings: SettingsDependency,
) -> EvidenceResponse:
    response.headers["Cache-Control"] = "no-store"
    user_id, workspace_id = current_account.user.id, current_account.workspace.id
    evidence = await database.scalar(
        select(ObservationEvidence)
        .join(OperationalObservation)
        .join(Connection, Connection.id == ObservationEvidence.connection_id)
        .where(
            ObservationEvidence.id == evidence_id,
            OperationalObservation.user_id == user_id,
            OperationalObservation.workspace_id == workspace_id,
            Connection.user_id == user_id,
            Connection.workspace_id == workspace_id,
        )
    )
    if evidence is None:
        raise unavailable(404, "Source evidence not found.")
    if evidence.provider != "google" or evidence.source_type != "gmail_message":
        raise unavailable(422, "Source excerpts are currently available for Gmail messages.")
    connection_id = evidence.connection_id

    async def authorize() -> Connection:
        return await readable_connection(
            database, connection_id=connection_id, user_id=user_id, workspace_id=workspace_id
        )

    try:
        async with asyncio.timeout(30):
            connection = await authorize()
            if await source_retry_after(database, connection_id, "gmail"):
                raise unavailable(429, "Gmail is temporarily rate limited. Try again later.")
            token = await access_token_for_connection(
                database, connection=connection, settings=settings
            )
            # Persist any narrowed OAuth grant, then recheck before reading mail.
            await database.commit()
            await authorize()
            if await source_retry_after(database, connection_id, "gmail"):
                raise unavailable(429, "Gmail is temporarily rate limited. Try again later.")
            document = await GoogleSourceGateway().gmail_message(
                token,
                workspace_id=workspace_id,
                connection_id=connection_id,
                external_id=evidence.external_resource_id,
                now=datetime.now(UTC),
            )
            # Concurrent pause/revocation must also prevent returning source text.
            await authorize()
    except TimeoutError:
        raise unavailable(504, "The source request timed out. Try again.") from None
    except GoogleAccessTokenError:
        raise unavailable(403, "Reconnect Gmail read access to view this source.") from None
    except GoogleSourceError:
        raise unavailable(502, "Gmail could not provide this source. Try again later.") from None
    if document.metadata.get("status") == "deleted":
        raise unavailable(410, "This email is no longer available in Gmail.")
    return EvidenceResponse(evidence_id=evidence_id, excerpts=verified_excerpts(evidence, document))
