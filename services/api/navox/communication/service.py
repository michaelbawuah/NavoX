from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.hashing import action_security_hash, canonical_hash
from navox.ai.foundation.contracts import AIResult
from navox.approvals.schemas import KnowledgeEmailSendContext, PrepareGmailSendRequest
from navox.approvals.service import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalPermissionError,
    ApprovalService,
    latest_approval,
)
from navox.communication.schemas import DraftContent
from navox.core.settings import Settings
from navox.db.communications import CommunicationDraft, CommunicationDraftVersion
from navox.db.knowledge import KnowledgeResource
from navox.db.models import (
    Action,
    Commitment,
    CommitmentSource,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    PlanStep,
    User,
    WorkspaceMembership,
)
from navox.knowledge.permissions import can_view_resource
from navox.providers.google_gmail import GmailReplyMetadata
from navox.providers.google_oauth import access_token_for_connection
from navox.providers.google_sources import GMAIL_READ_SCOPE, GoogleSourceGateway

COMMITMENT_BINDING = "COMMITMENT"
KNOWLEDGE_EMAIL_BINDING = "KNOWLEDGE_EMAIL"
EMAIL_RESOURCE_TYPE = "EMAIL"
GMAIL_READ_CAPABILITY = "communication.messages.read"
GOOGLE_MAIL_CONNECTOR_KEYS = frozenset({"google-gmail", "google-workspace"})
MESSAGE_RESOURCE_TYPE = "communication.message"


@dataclass(frozen=True)
class KnowledgeEmailBinding:
    """Server-resolved identity of one authorized Gmail message selector."""

    resource_id: UUID
    connection_id: UUID
    connector_connection_id: UUID
    external_id: str


async def knowledge_email_binding(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_id: UUID,
    now: datetime | None = None,
) -> KnowledgeEmailBinding:
    """Re-resolve a SPEC-007 ``EMAIL`` selector under the current account.

    The stored representation is never authority: the live resource, its
    canonical connector source, the connector definition and the legacy Google
    connection are all re-read here. Any change denies the action.
    """
    moment = now or datetime.now(UTC)
    resource = await database.scalar(
        select(KnowledgeResource)
        .where(
            KnowledgeResource.id == resource_id,
            KnowledgeResource.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if resource is None or resource.deleted_at is not None:
        raise ApprovalNotFoundError("The selected email source is unavailable")
    if resource.source_type != EMAIL_RESOURCE_TYPE:
        # A whole thread is not an exact message selector and must be resolved
        # to one message by a later phase.
        raise ApprovalPermissionError("Select one authorized email message, not a thread")
    if resource.owner_user_id != user_id:
        raise ApprovalPermissionError("The selected email source belongs to another account")
    if resource.source_read_capability != GMAIL_READ_CAPABILITY:
        raise ApprovalPermissionError("The selected email source is not a Gmail source")
    if not await can_view_resource(
        database,
        resource_id=resource_id,
        workspace_id=workspace_id,
        user_id=user_id,
        now=moment,
    ):
        raise ApprovalPermissionError("The selected email source is no longer authorized")
    if resource.source_resource_id is None:
        raise ApprovalPermissionError("The selected email source has no current message")

    connector = await database.scalar(
        select(ConnectorConnection)
        .where(
            ConnectorConnection.id == resource.source_connection_id,
            ConnectorConnection.workspace_id == workspace_id,
            ConnectorConnection.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        connector is None
        or connector.provider != "google"
        or connector.legacy_connection_id is None
    ):
        raise ApprovalPermissionError("The selected email source is not a Gmail source")
    definition = await database.scalar(
        select(ConnectorDefinition)
        .where(ConnectorDefinition.id == connector.connector_definition_id)
        .execution_options(populate_existing=True)
    )
    if (
        definition is None
        or not definition.active
        or definition.connector_key not in GOOGLE_MAIL_CONNECTOR_KEYS
    ):
        raise ApprovalPermissionError("The selected email source is not a Gmail source")

    legacy = await database.scalar(
        select(Connection)
        .where(Connection.id == connector.legacy_connection_id)
        .execution_options(populate_existing=True)
    )
    if (
        legacy is None
        or legacy.status != "active"
        or legacy.provider != "google"
        or (legacy.workspace_id, legacy.user_id) != (workspace_id, user_id)
        or GMAIL_READ_SCOPE not in list(legacy.granted_scopes or [])
    ):
        raise ApprovalPermissionError("The Gmail source connection is no longer active")

    canonical = await database.scalar(
        select(ConnectorResource)
        .where(
            ConnectorResource.id == resource.source_resource_id,
            ConnectorResource.workspace_id == workspace_id,
            ConnectorResource.connector_connection_id == connector.id,
            ConnectorResource.external_id == resource.external_resource_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        canonical is None
        or canonical.deleted
        or canonical.provider != "google"
        or canonical.resource_type.casefold() != MESSAGE_RESOURCE_TYPE
    ):
        raise ApprovalPermissionError("The selected email source is no longer current")
    return KnowledgeEmailBinding(
        resource_id=resource.id,
        connection_id=legacy.id,
        connector_connection_id=connector.id,
        external_id=resource.external_resource_id,
    )


async def authorize_knowledge_email_source(
    database: AsyncSession, draft: CommunicationDraft
) -> KnowledgeEmailBinding:
    """Recheck a knowledge-email draft against its freshly resolved source."""
    try:
        resource_id = UUID(draft.source_reference or "")
    except ValueError:
        raise ApprovalPermissionError("Draft source provenance is unavailable") from None
    binding = await knowledge_email_binding(
        database,
        workspace_id=draft.workspace_id,
        user_id=draft.user_id,
        resource_id=resource_id,
    )
    if (
        binding.connection_id != draft.source_connection_id
        or binding.external_id != draft.source_external_id
    ):
        raise ApprovalPermissionError("The draft source binding changed")
    return binding


async def owned_draft(
    database: AsyncSession,
    draft_id: UUID,
    *,
    user_id: UUID,
    workspace_id: UUID,
) -> CommunicationDraft:
    row = await database.scalar(
        select(CommunicationDraft).where(
            CommunicationDraft.id == draft_id,
            CommunicationDraft.user_id == user_id,
            CommunicationDraft.workspace_id == workspace_id,
        )
    )
    if row is None or await database.get(WorkspaceMembership, (workspace_id, user_id)) is None:
        raise ApprovalNotFoundError("Communication draft not found")
    return row


async def authorize_source(
    database: AsyncSession, draft: CommunicationDraft, *, lock: bool = False
) -> None:
    statement = (
        select(User).where(User.id == draft.user_id).execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    user = await database.scalar(statement)
    member = await database.get(
        WorkspaceMembership, (draft.workspace_id, draft.user_id), populate_existing=True
    )
    if user is None or user.agent_paused or member is None:
        raise ApprovalPermissionError("Draft source authorization is unavailable")
    if (draft.binding_kind or COMMITMENT_BINDING) == KNOWLEDGE_EMAIL_BINDING:
        await authorize_knowledge_email_source(database, draft)
        return
    connection = (
        await database.get(Connection, draft.source_connection_id, populate_existing=True)
        if draft.source_connection_id
        else None
    )
    if (
        connection is None
        or connection.status != "active"
        or connection.provider != "google"
        or (connection.workspace_id, connection.user_id) != (draft.workspace_id, draft.user_id)
        or "https://www.googleapis.com/auth/gmail.readonly" not in connection.granted_scopes
    ):
        raise ApprovalPermissionError("Draft source authorization is unavailable")
    try:
        source_id = UUID(draft.source_reference or "")
    except ValueError:
        raise ApprovalPermissionError("Draft source provenance is unavailable") from None
    source = await database.get(CommitmentSource, source_id, populate_existing=True)
    commitment = await database.get(Commitment, draft.commitment_id, populate_existing=True)
    if (
        source is None
        or source.connection_id != connection.id
        or source.commitment_id != draft.commitment_id
        or source.provider != "google"
        or commitment is None
        or (commitment.workspace_id, commitment.user_id) != (draft.workspace_id, draft.user_id)
    ):
        raise ApprovalPermissionError("Draft source provenance is unavailable")


async def current_version(
    database: AsyncSession, draft: CommunicationDraft
) -> CommunicationDraftVersion:
    row = await database.get(CommunicationDraftVersion, (draft.id, draft.current_version))
    if row is None:
        raise ApprovalNotFoundError("Draft version not found")
    return row


def content_of(version: CommunicationDraftVersion) -> DraftContent:
    return DraftContent(
        to=version.to,
        cc=version.cc,
        bcc=version.bcc,
        subject=version.subject,
        body=version.body,
        attachment_refs=version.attachment_refs,
    )


def content_hash(content: DraftContent) -> str:
    return canonical_hash(content.model_dump(mode="json"))


async def save_version(
    database: AsyncSession,
    draft: CommunicationDraft,
    content: DraftContent,
    *,
    expected_version: int,
    generated: AIResult | None = None,
) -> CommunicationDraftVersion:
    # Lock action before draft, matching approval consumption. An edit that wins
    # this race revokes the old approval; an executing send cannot be rewritten.
    await authorize_source(database, draft, lock=True)
    content = DraftContent.model_validate(content)
    if generated is not None and (
        (generated.workspace_id, generated.user_id) != (draft.workspace_id, draft.user_id)
        or not (
            generated.schema_validated
            and generated.semantic_validated
            and generated.policy_validated
        )
    ):
        raise ApprovalPermissionError("Draft generation is not validated for this account")
    action = None
    previous_action_id = draft.action_id
    if previous_action_id:
        action = await database.scalar(
            select(Action)
            .where(Action.id == draft.action_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    refreshed = await database.scalar(
        select(CommunicationDraft)
        .where(CommunicationDraft.id == draft.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if refreshed is None:
        raise ApprovalNotFoundError("Draft is no longer available")
    draft = refreshed
    if draft.action_id != previous_action_id or draft.current_version != expected_version:
        raise ApprovalConflictError("Draft changed; reload before saving")
    if (draft.binding_kind or COMMITMENT_BINDING) == KNOWLEDGE_EMAIL_BINDING:
        # The reply recipient is derived from the source message author, so an
        # edit may change wording but never who the reply is addressed to.
        previous = await database.scalar(
            select(CommunicationDraftVersion)
            .where(
                CommunicationDraftVersion.draft_id == draft.id,
                CommunicationDraftVersion.version == draft.current_version,
            )
            .execution_options(populate_existing=True)
        )
        if previous is None:
            raise ApprovalNotFoundError("Draft version not found")
        if str(content.to[0]).casefold() != str(previous.to[0]).casefold():
            raise ApprovalPermissionError(
                "The reply recipient is fixed by the authorized source message"
            )
    if action is not None:
        if action.status in {"executing", "completed", "uncertain"}:
            raise ApprovalConflictError("This send has already started; create a new draft")
        approval = await latest_approval(database, action.id)
        if approval is not None:
            approval.status = "superseded"
            approval.superseded_at = datetime.now(UTC)
        action.status = "blocked"
        action.policy_reason = "draft_version_changed"
        from navox.approvals.service import plan_for_action

        plan, step = await plan_for_action(database, action)
        plan.status, step.status, plan.error_code = "blocked", "blocked", "draft_version_changed"
        draft.action_id = None
    draft.current_version += 1
    draft.status = "review"
    draft.approved_version = None
    draft.approved_payload_hash = None
    version = make_version(draft, content, generated)
    database.add(version)
    await database.commit()
    return version


def make_version(
    draft: CommunicationDraft,
    content: DraftContent,
    generated: AIResult | None,
) -> CommunicationDraftVersion:
    return CommunicationDraftVersion(
        draft_id=draft.id,
        version=draft.current_version,
        **content.model_dump(mode="json"),
        generated_provider=generated.provider.value if generated else None,
        generated_model=generated.model if generated else None,
        created_by="AI" if generated else "USER",
        payload_hash=content_hash(content),
    )


async def create_draft(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    commitment_id: UUID,
    source_connection_id: UUID,
    source_reference: str,
    content: DraftContent,
    generated: AIResult,
) -> CommunicationDraft:
    draft = CommunicationDraft(
        id=uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        commitment_id=commitment_id,
        binding_kind=COMMITMENT_BINDING,
        source_connection_id=source_connection_id,
        source_reference=source_reference,
        current_version=1,
        status="review",
    )
    return await persist_new_draft(database, draft, content=content, generated=generated)


async def create_knowledge_email_draft(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    binding: KnowledgeEmailBinding,
    content: DraftContent,
    generated: AIResult,
) -> CommunicationDraft:
    draft = CommunicationDraft(
        id=uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        commitment_id=None,
        binding_kind=KNOWLEDGE_EMAIL_BINDING,
        source_connection_id=binding.connection_id,
        source_reference=str(binding.resource_id),
        source_external_id=binding.external_id,
        current_version=1,
        status="review",
    )
    return await persist_new_draft(database, draft, content=content, generated=generated)


async def persist_new_draft(
    database: AsyncSession,
    draft: CommunicationDraft,
    *,
    content: DraftContent,
    generated: AIResult,
) -> CommunicationDraft:
    await authorize_source(database, draft, lock=True)
    if (generated.workspace_id, generated.user_id) != (draft.workspace_id, draft.user_id) or not (
        generated.schema_validated and generated.semantic_validated and generated.policy_validated
    ):
        raise ApprovalPermissionError("Draft generation is not validated for this account")
    database.add(draft)
    await database.flush()
    database.add(make_version(draft, content, generated))
    await database.commit()
    return draft


async def knowledge_email_reply_metadata(
    database: AsyncSession,
    draft: CommunicationDraft,
    *,
    content: DraftContent,
    settings: Settings | None,
) -> tuple[KnowledgeEmailBinding, GmailReplyMetadata]:
    """Recheck the live source, then resolve reply metadata for this exact draft."""
    if settings is None:
        raise ApprovalPermissionError("Gmail reply metadata requires provider settings")
    binding = await authorize_knowledge_email_source(database, draft)
    connection = await database.get(Connection, binding.connection_id)
    if connection is None or not connection.external_email:
        raise ApprovalPermissionError("Gmail reply source is unavailable")
    token = await access_token_for_connection(database, connection=connection, settings=settings)
    # The binding is rechecked on both sides of the provider call: a revocation
    # that wins the race must not leave a usable prepared reply behind.
    await authorize_source(database, draft)
    reply = await GoogleSourceGateway().gmail_reply_metadata(token, external_id=binding.external_id)
    await authorize_source(database, draft)
    if reply.source_message_id != binding.external_id:
        raise ApprovalConflictError("Gmail reply source changed")
    if not reply.matches_subject(content.subject):
        raise ApprovalConflictError("Reply subject must match the source thread")
    if (
        reply.source_author is None
        or reply.source_author == connection.external_email.casefold()
        or str(content.to[0]).casefold() != reply.source_author
    ):
        raise ApprovalPermissionError("The reply recipient must match the authorized source author")
    return binding, reply


async def prepare_draft(
    database: AsyncSession,
    draft: CommunicationDraft,
    *,
    expected_version: int,
    connection_id: UUID | None,
    request_id: UUID,
    reply_to_source: bool = False,
    settings: Settings | None = None,
) -> Action:
    await authorize_source(database, draft, lock=True)
    refreshed = await database.scalar(
        select(CommunicationDraft)
        .where(CommunicationDraft.id == draft.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if refreshed is None:
        raise ApprovalNotFoundError("Draft is no longer available")
    draft = refreshed
    if draft.current_version != expected_version:
        raise ApprovalConflictError("Draft changed or already has a prepared send")
    knowledge_email = (draft.binding_kind or COMMITMENT_BINDING) == KNOWLEDGE_EMAIL_BINDING
    # A knowledge-email draft derives its mailbox from the authorized source; the
    # client never supplies one. Every other binding still names its connection.
    if knowledge_email:
        if connection_id is not None and connection_id != draft.source_connection_id:
            raise ApprovalPermissionError("Reply must use the authorized source Gmail account")
        prepared_connection_id = draft.source_connection_id
    else:
        if connection_id is None:
            raise ApprovalPermissionError("A Gmail connection is required to prepare this draft")
        prepared_connection_id = connection_id
    if prepared_connection_id is None:
        raise ApprovalPermissionError("Draft source authorization is unavailable")
    if draft.action_id is not None:
        existing = await database.get(Action, draft.action_id)
        if existing is not None:
            from navox.approvals.service import plan_for_action

            plan, _ = await plan_for_action(database, existing)
            if (
                plan.request_id == request_id
                and str(existing.payload.get("connection_id")) == str(prepared_connection_id)
                and (existing.payload.get("reply") is not None) == reply_to_source
            ):
                return existing
        raise ApprovalConflictError("This version already has a prepared send")
    version = await current_version(database, draft)
    content = content_of(version)
    reply = None
    source_context = None
    if knowledge_email:
        if not reply_to_source:
            raise ApprovalPermissionError(
                "A knowledge-email draft must be prepared as a reply to its source message"
            )
        binding, reply = await knowledge_email_reply_metadata(
            database, draft, content=content, settings=settings
        )
        source_context = KnowledgeEmailSendContext(
            draft_id=draft.id,
            knowledge_resource_id=binding.resource_id,
            source_message_id=binding.external_id,
        )
    elif reply_to_source:
        if settings is None or connection_id != draft.source_connection_id:
            raise ApprovalPermissionError("Reply must use the authorized source Gmail account")
        source = await database.get(CommitmentSource, UUID(draft.source_reference or ""))
        connection = await database.get(Connection, connection_id)
        if (
            source is None
            or source.source_type != "gmail_message"
            or not source.external_resource_id
            or connection is None
        ):
            raise ApprovalPermissionError("Gmail reply source is unavailable")
        token = await access_token_for_connection(
            database, connection=connection, settings=settings
        )
        await authorize_source(database, draft)
        reply = await GoogleSourceGateway().gmail_reply_metadata(
            token, external_id=source.external_resource_id
        )
        await authorize_source(database, draft)
        if reply.source_message_id != source.external_resource_id:
            raise ApprovalPermissionError("Gmail reply source changed")
        if not reply.matches_subject(content.subject):
            raise ApprovalConflictError("Reply subject must match the source thread")
    action, approval, created = await ApprovalService().prepare_gmail_send(
        database,
        user_id=draft.user_id,
        workspace_id=draft.workspace_id,
        commitment_id=draft.commitment_id,
        commit=False,
        source_context=source_context,
        request=PrepareGmailSendRequest(
            request_id=request_id,
            connection_id=prepared_connection_id,
            to=content.to[0],
            subject=content.subject,
            body_text=content.body,
            post_send_state="unchanged" if knowledge_email else "waiting",
        ),
    )
    if not created:
        raise ApprovalConflictError("Use a new request ID for this draft version")
    payload = dict(
        action.payload,
        draft_id=str(draft.id),
        draft_version=draft.current_version,
        draft_payload_hash=version.payload_hash,
    )
    if reply is not None:
        payload["reply"] = reply.model_dump(mode="json")
    action.payload = payload
    action.payload_hash = action_security_hash(
        provider="google", action_type="gmail.send", payload=payload
    )
    approval.action_payload_hash = action.payload_hash
    step = await database.get(PlanStep, action.plan_step_id)
    if step is not None:
        step.input_payload = payload
    draft.action_id = action.id
    draft.status = "awaiting_approval"
    await database.commit()
    return action


async def expected_source_message_id(
    database: AsyncSession, draft: CommunicationDraft
) -> str | None:
    """Live external Gmail message id for whichever binding the draft uses."""
    if (draft.binding_kind or COMMITMENT_BINDING) == KNOWLEDGE_EMAIL_BINDING:
        return (await authorize_knowledge_email_source(database, draft)).external_id
    try:
        source_id = UUID(draft.source_reference or "")
    except ValueError:
        return None
    source = await database.get(CommitmentSource, source_id)
    if (
        source is None
        or source.source_type != "gmail_message"
        or source.connection_id != draft.source_connection_id
        or not source.external_resource_id
    ):
        return None
    return source.external_resource_id


async def valid_draft_binding(database: AsyncSession, action: Action, *, approved: bool) -> bool:
    value = action.payload.get("draft_id")
    if value is None:
        return True
    try:
        draft_id = UUID(str(value))
    except ValueError:
        return False
    draft = await database.scalar(
        select(CommunicationDraft)
        .where(
            CommunicationDraft.id == draft_id,
            CommunicationDraft.workspace_id == action.workspace_id,
            CommunicationDraft.user_id == action.user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        draft is None
        or draft.action_id != action.id
        or draft.current_version != action.payload.get("draft_version")
    ):
        return False
    try:
        await authorize_source(database, draft)
    except ApprovalPermissionError:
        return False
    try:
        version = await current_version(database, draft)
        content = content_of(version)
    except (ApprovalNotFoundError, ValueError):
        return False
    if (
        version.payload_hash != content_hash(content)
        or version.payload_hash != action.payload.get("draft_payload_hash")
        or action.payload.get("to") != str(content.to[0])
        or action.payload.get("subject") != content.subject
        or action.payload.get("body_text") != content.body
    ):
        return False
    if action.payload.get("reply") is not None:
        try:
            reply = GmailReplyMetadata.model_validate(action.payload["reply"])
        except ValueError:
            return False
        try:
            source_external_id = await expected_source_message_id(database, draft)
        except ApprovalPermissionError:
            return False
        if (
            source_external_id is None
            or source_external_id != reply.source_message_id
            or str(draft.source_connection_id) != action.payload.get("connection_id")
            or not reply.matches_subject(content.subject)
        ):
            return False
        if (draft.binding_kind or COMMITMENT_BINDING) == KNOWLEDGE_EMAIL_BINDING and (
            reply.source_author is None or str(content.to[0]).casefold() != reply.source_author
        ):
            return False
    return not approved or (
        draft.approved_version == version.version
        and draft.approved_payload_hash == action.payload_hash
    )


async def check_draft_approval(
    database: AsyncSession,
    action: Action,
    *,
    version: int | None,
    payload_hash: str | None,
) -> None:
    if version != action.payload.get("draft_version") or payload_hash != action.payload_hash:
        raise ApprovalConflictError(
            "Approval must match the exact reviewed draft version and payload"
        )
    if not await valid_draft_binding(database, action, approved=False):
        raise ApprovalConflictError("Draft changed; review and approve its current version")


async def approve_draft_binding(
    database: AsyncSession,
    action: Action,
    *,
    version: int | None,
    payload_hash: str | None,
) -> None:
    await check_draft_approval(database, action, version=version, payload_hash=payload_hash)
    draft = await database.get(CommunicationDraft, UUID(str(action.payload["draft_id"])))
    if draft is None:
        raise ApprovalNotFoundError("Draft not found")
    draft.approved_version = version
    draft.approved_payload_hash = payload_hash
    draft.status = "approved"
