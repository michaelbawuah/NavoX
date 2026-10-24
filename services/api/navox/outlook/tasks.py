"""Selected Outlook email uses the existing reviewed task/provenance contracts."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.contracts import CanonicalResource, stable_resource_id
from navox.connectors.runtime import resource_hash
from navox.core.settings import Settings
from navox.db.models import ConnectorConnection, ConnectorResource
from navox.db.student_email import StudentEmailReview
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.intelligence.extraction import source_document_hash
from navox.outlook import service
from navox.outlook.schemas import TaskPreview
from navox.providers.microsoft_mail import MailMessage
from navox.student import service as student
from navox.student_email import service as email
from navox.student_email.schemas import AcceptInput, AnalyzeInput, CorrectionInput, EraseInput


def enabled(settings: Settings, *, accepting: bool = False) -> None:
    if not settings.student_email_enabled:
        raise service.OutlookError("Email task review is not available here yet", 503)
    if accepting and not settings.student_planning_enabled:
        raise service.OutlookError("Saving tasks is not available here yet", 503)


def document_of(
    message: MailMessage, *, scope: service.Scope, connector_id: UUID
) -> SourceDocument:
    return SourceDocument(
        id=stable_resource_id(connector_id, "communication.message", message.id),
        workspace_id=scope.workspace_id,
        provider="microsoft",
        source_type="outlook_message",
        external_id=message.id,
        external_parent_id=message.conversation_id or None,
        author=SourceIdentity(
            provider="microsoft", identity_type="email", identity_value=message.sender
        ),
        recipients=[
            SourceIdentity(provider="microsoft", identity_type="email", identity_value=value)
            for value in message.to
        ],
        subject=message.subject,
        content=message.body,
        occurred_at=message.sent_at,
        retrieved_at=service.now(),
        metadata={"status": "active"},
    )


def canonical_document(document: SourceDocument, connector_id: UUID) -> CanonicalResource:
    """Only the digest and provider locator are stored; no sender, subject or body."""
    return CanonicalResource(
        resource_id=stable_resource_id(connector_id, "communication.message", document.external_id),
        workspace_id=document.workspace_id,
        connector_connection_id=connector_id,
        provider="microsoft",
        resource_type="communication.message",
        external_id=document.external_id,
        external_parent_id=document.external_parent_id,
        canonical={"status": "active", "content_persisted": False},
        provider_metadata={"source_hash": source_document_hash(document)},
        created_at=document.occurred_at,
        retrieved_at=document.retrieved_at,
    )


class SelectedMessageReader:
    """Pass the one already fetched message through shared review validation."""

    def __init__(self, scope: service.Scope, document: SourceDocument, generation: int) -> None:
        self.scope, self.document, self.generation = scope, document, generation

    async def read(
        self,
        database: AsyncSession,
        settings: Settings,
        *,
        user_id: UUID,
        workspace_id: UUID,
        connector_id: UUID,
        legacy_id: UUID,
        external_id: str,
        source_id: UUID,
        source_hash: str,
    ) -> SourceDocument:
        if (user_id, workspace_id, external_id, source_id) != (
            self.scope.user_id,
            self.scope.workspace_id,
            self.document.external_id,
            self.document.id,
        ):
            raise service.OutlookError("The selected email changed. Reopen it", 409)
        row = await service.recheck(database, self.scope, connector_id, self.generation)
        if row.legacy_connection_id != legacy_id:
            raise service.OutlookError("The selected mailbox changed. Reopen the email", 409)
        # No provider call is made here, and no caller-supplied message is trusted.
        return self.document


async def owned_review(
    database: AsyncSession, scope: service.Scope, review_id: UUID
) -> StudentEmailReview:
    await service.authority(database, scope, processing=False)
    review = await email.owned_review(
        database, user_id=scope.user_id, workspace_id=scope.workspace_id, review_id=review_id
    )
    if review.connector_id is None:
        raise service.OutlookError(
            "This email review was cleared. Open its email to start again", 410
        )
    row = await database.get(ConnectorConnection, review.connector_id, populate_existing=True)
    if (
        row is None
        or row.provider != "microsoft"
        or (row.user_id, row.workspace_id) != (scope.user_id, scope.workspace_id)
    ):
        raise service.OutlookError("This Outlook review is unavailable", 404)
    return review


async def preview(
    database: AsyncSession, settings: Settings, scope: service.Scope, payload: TaskPreview
) -> dict[str, Any]:
    enabled(settings)
    await service.connection(database, scope, payload.connection_id)
    source_id = stable_resource_id(
        payload.connection_id, "communication.message", payload.source_message_id
    )
    previous = await database.scalar(
        select(StudentEmailReview).where(
            StudentEmailReview.user_id == scope.user_id,
            StudentEmailReview.workspace_id == scope.workspace_id,
            StudentEmailReview.request_id == payload.request_id,
        )
    )
    if previous is not None:
        if previous.connector_id != payload.connection_id or previous.source_id != source_id:
            raise service.OutlookError("This review request already belongs to another email", 409)
        return await read(database, settings, scope, previous.id)
    await database.commit()
    connector, message, generation = await service.read_message(
        database, settings, scope, payload.connection_id, payload.source_message_id
    )
    document = document_of(message, scope=scope, connector_id=connector.id)
    canonical = canonical_document(document, connector.id)
    source_hash = resource_hash(canonical)
    registered = await database.get(ConnectorResource, source_id, populate_existing=True)
    if registered is None:
        registered = ConnectorResource(
            id=source_id,
            workspace_id=scope.workspace_id,
            connector_connection_id=connector.id,
            provider="microsoft",
            resource_type="communication.message",
            external_id=message.id,
            content_hash=source_hash,
            retrieved_at=canonical.retrieved_at,
        )
        database.add(registered)
    elif (
        registered.workspace_id,
        registered.connector_connection_id,
        registered.provider,
        registered.external_id,
    ) != (scope.workspace_id, connector.id, "microsoft", message.id):
        raise service.OutlookError("The email source could not be verified", 409)
    registered.canonical = {"status": "active", "content_persisted": False}
    registered.provider_metadata = {"source_hash": source_document_hash(document)}
    registered.external_parent_id = message.conversation_id or None
    registered.source_created_at = message.sent_at
    registered.source_url = None
    registered.content_hash, registered.retrieved_at = source_hash, canonical.retrieved_at
    registered.deleted = False
    await database.flush()
    review = await email.analyze(
        database,
        settings,
        user_id=scope.user_id,
        workspace_id=scope.workspace_id,
        payload=AnalyzeInput(
            request_id=payload.request_id, source_id=source_id, source_hash=source_hash
        ),
        reader=SelectedMessageReader(scope, document, generation),
    )
    await service.recheck(database, scope, connector.id, generation)
    return review


async def read(
    database: AsyncSession, settings: Settings, scope: service.Scope, review_id: UUID
) -> dict[str, Any]:
    enabled(settings)
    await owned_review(database, scope, review_id)
    result = await email.read_review(
        database,
        settings,
        user_id=scope.user_id,
        workspace_id=scope.workspace_id,
        review_id=review_id,
    )
    await service.authority(database, scope)
    return result


async def correct(
    database: AsyncSession,
    settings: Settings,
    scope: service.Scope,
    review_id: UUID,
    payload: CorrectionInput,
) -> dict[str, Any]:
    enabled(settings)
    await owned_review(database, scope, review_id)
    result = await email.correct(
        database,
        settings,
        user_id=scope.user_id,
        workspace_id=scope.workspace_id,
        review_id=review_id,
        payload=payload,
    )
    await service.authority(database, scope)
    return result


async def accept(
    database: AsyncSession,
    settings: Settings,
    scope: service.Scope,
    review_id: UUID,
    payload: AcceptInput,
) -> dict[str, Any]:
    enabled(settings, accepting=True)
    await owned_review(database, scope, review_id)
    result = await email.accept_task(
        database,
        settings,
        user_id=scope.user_id,
        workspace_id=scope.workspace_id,
        review_id=review_id,
        payload=payload,
    )
    await service.authority(database, scope)
    return result


async def erase(
    database: AsyncSession, scope: service.Scope, review_id: UUID, payload: EraseInput
) -> dict[str, object]:
    # Clearing private data remains available while features or providers are off.
    await service.authority(database, scope, processing=False)
    return await email.erase_review(
        database,
        user_id=scope.user_id,
        workspace_id=scope.workspace_id,
        review_id=review_id,
        revision=payload.revision,
    )


async def forget(
    database: AsyncSession, scope: service.Scope, source_id: UUID
) -> dict[str, object]:
    await service.authority(database, scope, processing=False)
    source = await database.scalar(
        select(ConnectorResource)
        .join(ConnectorConnection)
        .where(
            ConnectorResource.id == source_id,
            ConnectorResource.workspace_id == scope.workspace_id,
            ConnectorResource.provider == "microsoft",
            ConnectorConnection.user_id == scope.user_id,
            ConnectorConnection.workspace_id == scope.workspace_id,
            ConnectorConnection.provider == "microsoft",
        )
    )
    if source is None:
        raise service.OutlookError("This Outlook task source is unavailable", 404)
    result = await email.forget_email_data(
        database, user_id=scope.user_id, workspace_id=scope.workspace_id, source_id=source_id
    )
    await database.commit()
    return result


async def context(
    database: AsyncSession, settings: Settings, scope: service.Scope
) -> dict[str, Any]:
    enabled(settings)
    await service.authority(database, scope)
    timetable = await student.timetable(database, scope.user_id, scope.workspace_id)
    return timetable.model_dump(mode="json")
