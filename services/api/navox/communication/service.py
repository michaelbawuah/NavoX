from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.hashing import action_security_hash, canonical_hash
from navox.ai.foundation.contracts import AIResult
from navox.approvals.schemas import PrepareGmailSendRequest
from navox.approvals.service import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalPermissionError,
    ApprovalService,
    latest_approval,
)
from navox.communication.schemas import DraftContent
from navox.db.communications import CommunicationDraft, CommunicationDraftVersion
from navox.db.models import (
    Action,
    Commitment,
    CommitmentSource,
    Connection,
    PlanStep,
    User,
    WorkspaceMembership,
)


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
    connection = (
        await database.get(Connection, draft.source_connection_id, populate_existing=True)
        if draft.source_connection_id
        else None
    )
    if (
        user is None
        or user.agent_paused
        or member is None
        or connection is None
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
        source_connection_id=source_connection_id,
        source_reference=source_reference,
        current_version=1,
        status="review",
    )
    await authorize_source(database, draft, lock=True)
    if (generated.workspace_id, generated.user_id) != (workspace_id, user_id) or not (
        generated.schema_validated and generated.semantic_validated and generated.policy_validated
    ):
        raise ApprovalPermissionError("Draft generation is not validated for this account")
    database.add(draft)
    await database.flush()
    database.add(make_version(draft, content, generated))
    await database.commit()
    return draft


async def prepare_draft(
    database: AsyncSession,
    draft: CommunicationDraft,
    *,
    expected_version: int,
    connection_id: UUID,
    request_id: UUID,
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
    if draft.action_id is not None:
        existing = await database.get(Action, draft.action_id)
        if existing is not None:
            from navox.approvals.service import plan_for_action

            plan, _ = await plan_for_action(database, existing)
            if plan.request_id == request_id and str(existing.payload.get("connection_id")) == str(
                connection_id
            ):
                return existing
        raise ApprovalConflictError("This version already has a prepared send")
    version = await current_version(database, draft)
    content = content_of(version)
    action, approval, created = await ApprovalService().prepare_gmail_send(
        database,
        user_id=draft.user_id,
        workspace_id=draft.workspace_id,
        commitment_id=draft.commitment_id,
        commit=False,
        request=PrepareGmailSendRequest(
            request_id=request_id,
            connection_id=connection_id,
            to=content.to[0],
            subject=content.subject,
            body_text=content.body,
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
