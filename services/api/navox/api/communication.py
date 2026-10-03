from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from navox.ai.configured import build_runtime
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.runtime import GatewayUnavailable
from navox.api.actions import (
    ActionDetailResponse,
    ApprovalDispatcherDependency,
    action_response,
    approval_error,
    best_effort_signal,
    ensure_workflow,
)
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.approvals.schemas import ApprovalDecisionRequest
from navox.approvals.service import ApprovalService, ApprovalServiceError
from navox.communication.generation import (
    generate_content,
    generate_knowledge_email_content,
)
from navox.communication.schemas import (
    DraftContent,
    GenerateDraft,
    GenerateKnowledgeEmailDraft,
    PrepareDraft,
    RegenerateDraft,
    ReviseDraft,
)
from navox.communication.service import (
    KNOWLEDGE_EMAIL_BINDING,
    content_of,
    create_draft,
    create_knowledge_email_draft,
    current_version,
    owned_draft,
    prepare_draft,
    save_version,
)
from navox.db.communications import CommunicationDraft, CommunicationDraftVersion
from navox.providers.google_oauth import GoogleAccessTokenError
from navox.providers.google_sources import GoogleSourceError

router = APIRouter(prefix="/communication-drafts", tags=["communication"])


async def detail(
    database: DatabaseSession, draft: CommunicationDraft, *, history: bool = True
) -> dict[str, object]:
    versions = (
        await database.scalars(
            select(CommunicationDraftVersion)
            .where(CommunicationDraftVersion.draft_id == draft.id)
            .order_by(CommunicationDraftVersion.version.desc())
            .limit(100 if history else 1)
        )
    ).all()
    return {
        "id": str(draft.id),
        "commitment_id": str(draft.commitment_id) if draft.commitment_id else None,
        "binding_kind": draft.binding_kind or "COMMITMENT",
        "source_id": draft.source_reference,
        "source_external_id": draft.source_external_id,
        "current_version": draft.current_version,
        "status": draft.status,
        "action_id": str(draft.action_id) if draft.action_id else None,
        "versions": [
            {
                "version": v.version,
                **content_of(v).model_dump(mode="json"),
                "created_by": v.created_by,
                "provider": v.generated_provider,
                "model": v.generated_model,
                "payload_hash": v.payload_hash,
            }
            for v in reversed(versions)
        ],
    }


@router.post("", status_code=201)
async def generate(
    payload: GenerateDraft,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    if account.user.agent_paused:
        raise HTTPException(409, "NavoX is paused")
    try:
        content, result, source_connection_id = await generate_content(
            database,
            await build_runtime(settings),
            settings=settings,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            commitment_id=payload.commitment_id,
            source_id=payload.source_id,
            recipient=str(payload.recipient),
            instructions=payload.instructions,
        )
        draft = await create_draft(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            commitment_id=payload.commitment_id,
            source_connection_id=source_connection_id,
            source_reference=str(payload.source_id),
            content=content,
            generated=result,
        )
        return await detail(database, draft)
    except ApprovalServiceError as error:
        raise approval_error(error) from None
    except (
        AIProviderNotConfigured,
        GatewayUnavailable,
        GoogleSourceError,
        GoogleAccessTokenError,
        TimeoutError,
    ):
        raise HTTPException(
            503, "No eligible AI provider is available; you can still write the email yourself"
        ) from None


@router.post("/from-knowledge-email", status_code=201)
async def generate_from_knowledge_email(
    payload: GenerateKnowledgeEmailDraft,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    """Draft a reply from one authorized SPEC-007 email selector."""
    if account.user.agent_paused:
        raise HTTPException(409, "NavoX is paused")
    try:
        content, result, binding = await generate_knowledge_email_content(
            database,
            await build_runtime(settings),
            settings=settings,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            source_id=payload.source_id,
            instructions=payload.instructions,
        )
        draft = await create_knowledge_email_draft(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            binding=binding,
            content=content,
            generated=result,
        )
        return await detail(database, draft)
    except ApprovalServiceError as error:
        raise approval_error(error) from None
    except (
        AIProviderNotConfigured,
        GatewayUnavailable,
        GoogleSourceError,
        GoogleAccessTokenError,
        TimeoutError,
    ):
        raise HTTPException(
            503, "No eligible AI provider is available; you can still write the email yourself"
        ) from None


@router.get("")
async def list_drafts(
    account: CurrentAccountDependency, database: DatabaseSession
) -> list[dict[str, object]]:
    rows = (
        await database.scalars(
            select(CommunicationDraft)
            .where(
                CommunicationDraft.workspace_id == account.workspace.id,
                CommunicationDraft.user_id == account.user.id,
            )
            .order_by(CommunicationDraft.created_at.desc())
            .limit(100)
        )
    ).all()
    return [await detail(database, draft, history=False) for draft in rows]


@router.get("/sources/{commitment_id}")
async def list_sources(
    commitment_id: UUID, account: CurrentAccountDependency, database: DatabaseSession
) -> list[dict[str, str]]:
    from navox.db.models import Commitment, CommitmentSource, Connection

    rows = (
        await database.scalars(
            select(CommitmentSource)
            .join(Commitment)
            .join(Connection, CommitmentSource.connection_id == Connection.id)
            .where(
                Commitment.id == commitment_id,
                Commitment.workspace_id == account.workspace.id,
                Commitment.user_id == account.user.id,
                Connection.workspace_id == account.workspace.id,
                Connection.user_id == account.user.id,
                Connection.status == "active",
                CommitmentSource.provider == "google",
                CommitmentSource.source_type == "gmail_message",
            )
            .order_by(CommitmentSource.extracted_at.desc())
            .limit(20)
        )
    ).all()
    return [
        {"id": str(row.id), "external_id": row.external_resource_id or "", "provider": "Gmail"}
        for row in rows
    ]


@router.get("/{draft_id}")
async def get_draft(
    draft_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
) -> dict[str, object]:
    try:
        return await detail(
            database,
            await owned_draft(
                database, draft_id, user_id=account.user.id, workspace_id=account.workspace.id
            ),
        )
    except ApprovalServiceError as error:
        raise approval_error(error) from None


@router.patch("/{draft_id}")
async def revise(
    draft_id: UUID,
    payload: ReviseDraft,
    account: CurrentAccountDependency,
    database: DatabaseSession,
) -> dict[str, object]:
    try:
        draft = await owned_draft(
            database, draft_id, user_id=account.user.id, workspace_id=account.workspace.id
        )
        content = DraftContent.model_validate(payload.model_dump(exclude={"expected_version"}))
        await save_version(database, draft, content, expected_version=payload.expected_version)
        return await detail(database, draft)
    except ApprovalServiceError as error:
        raise approval_error(error) from None


@router.post("/{draft_id}/regenerate")
async def regenerate(
    draft_id: UUID,
    payload: RegenerateDraft,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    if account.user.agent_paused:
        raise HTTPException(409, "NavoX is paused")
    try:
        draft = await owned_draft(
            database, draft_id, user_id=account.user.id, workspace_id=account.workspace.id
        )
        if draft.current_version != payload.expected_version:
            raise HTTPException(409, "Draft changed; reload before generating another version")
        previous = await current_version(database, draft)
        if (draft.binding_kind or "COMMITMENT") == KNOWLEDGE_EMAIL_BINDING:
            content, result, _ = await generate_knowledge_email_content(
                database,
                await build_runtime(settings),
                settings=settings,
                workspace_id=account.workspace.id,
                user_id=account.user.id,
                source_id=UUID(draft.source_reference or ""),
                previous=content_of(previous),
                instructions=payload.instructions,
            )
        else:
            if draft.commitment_id is None:
                raise HTTPException(409, "Draft is missing its commitment binding")
            content, result, _ = await generate_content(
                database,
                await build_runtime(settings),
                settings=settings,
                workspace_id=account.workspace.id,
                user_id=account.user.id,
                commitment_id=draft.commitment_id,
                source_id=UUID(draft.source_reference or ""),
                previous=content_of(previous),
                recipient=previous.to[0],
                instructions=payload.instructions,
            )
        await save_version(
            database, draft, content, expected_version=payload.expected_version, generated=result
        )
        return await detail(database, draft)
    except ApprovalServiceError as error:
        raise approval_error(error) from None
    except (
        AIProviderNotConfigured,
        GatewayUnavailable,
        GoogleSourceError,
        GoogleAccessTokenError,
        TimeoutError,
    ):
        raise HTTPException(
            503, "No eligible AI provider is available; your current draft is preserved"
        ) from None


@router.post("/{draft_id}/prepare", response_model=ActionDetailResponse)
async def prepare(
    draft_id: UUID,
    payload: PrepareDraft,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: ApprovalDispatcherDependency,
) -> ActionDetailResponse:
    try:
        draft = await owned_draft(
            database, draft_id, user_id=account.user.id, workspace_id=account.workspace.id
        )
        action = await prepare_draft(
            database,
            draft,
            expected_version=payload.expected_version,
            connection_id=payload.connection_id,
            request_id=payload.request_id,
            reply_to_source=payload.reply_to_source,
            settings=settings,
        )
        await ensure_workflow(dispatcher, database, action, settings)
        return await action_response(database, action)
    except ApprovalServiceError as error:
        raise approval_error(error) from None

    except (GoogleSourceError, GoogleAccessTokenError, TimeoutError):
        raise HTTPException(
            503, "Gmail reply metadata is unavailable; your draft is preserved"
        ) from None


@router.post("/{draft_id}/approve", response_model=ActionDetailResponse)
async def approve(
    draft_id: UUID,
    payload: ApprovalDecisionRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: ApprovalDispatcherDependency,
) -> ActionDetailResponse:
    try:
        draft = await owned_draft(
            database, draft_id, user_id=account.user.id, workspace_id=account.workspace.id
        )
        if draft.action_id is None:
            raise HTTPException(409, "Prepare the current draft version before approval")
        action, _ = await ApprovalService().approve(
            database,
            action_id=draft.action_id,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
            request_id=payload.request_id,
            expected_payload_hash=payload.expected_payload_hash,
            draft_version=payload.draft_version,
        )
        # No second approval or scheduling gate: signal the verified send workflow now.
        await ensure_workflow(dispatcher, database, action, settings)
        await best_effort_signal(
            dispatcher, action_id=action.id, decision="approved", settings=settings
        )
        await database.refresh(action)
        return await action_response(database, action)
    except ApprovalServiceError as error:
        raise approval_error(error) from None
