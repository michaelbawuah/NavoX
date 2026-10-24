from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import EmailStr, TypeAdapter, ValidationError
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
from navox.api.connector_management import require_origin
from navox.api.student import expected_owner
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
from navox.providers.google_sources import GMAIL_READ_SCOPE, GoogleSourceError

router = APIRouter(
    prefix="/communication-drafts", tags=["communication"], dependencies=[Depends(expected_owner)]
)
_MAILBOX = TypeAdapter(EmailStr)


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
    from navox.db.models import (
        Commitment,
        CommitmentSource,
        Connection,
        ConnectorConnection,
        ConnectorResource,
    )

    if account.user.agent_paused:
        return []
    # Establish current mailbox read authority before selecting source identities.
    # Stored source metadata is not authority and is deliberately not loaded here.
    connections = (
        await database.execute(
            select(Connection.id, Connection.external_email, Connection.granted_scopes).where(
                Connection.workspace_id == account.workspace.id,
                Connection.user_id == account.user.id,
                Connection.provider == "google",
                Connection.status == "active",
            )
        )
    ).all()
    mailboxes: dict[UUID, str | None] = {}
    for connection_id, external_email, scopes in connections:
        if GMAIL_READ_SCOPE not in (scopes or []):
            continue
        mailbox = None
        if isinstance(external_email, str) and not any(
            character in external_email for character in "\r\n<>"
        ):
            try:
                mailbox = str(_MAILBOX.validate_python(external_email))
            except ValidationError:
                pass
        mailboxes[connection_id] = mailbox
    if not mailboxes:
        return []

    # An existing canonical tombstone is a stronger signal than an old
    # commitment attachment. Match the mailbox as well as the message ID: Gmail
    # identifiers from another connected account cannot retire this source.
    known_deleted_source = (
        select(ConnectorResource.id)
        .join(
            ConnectorConnection,
            ConnectorResource.connector_connection_id == ConnectorConnection.id,
        )
        .where(
            ConnectorConnection.legacy_connection_id == CommitmentSource.connection_id,
            ConnectorConnection.workspace_id == account.workspace.id,
            ConnectorConnection.user_id == account.user.id,
            ConnectorConnection.provider == "google",
            ConnectorResource.workspace_id == account.workspace.id,
            ConnectorResource.provider == "google",
            ConnectorResource.resource_type == "communication.message",
            ConnectorResource.external_id == CommitmentSource.external_resource_id,
            ConnectorResource.deleted.is_(True),
        )
        .correlate(CommitmentSource)
        .exists()
    )
    rows = (
        await database.execute(
            select(
                CommitmentSource.id,
                CommitmentSource.connection_id,
                CommitmentSource.external_resource_id,
            )
            .join(Commitment)
            .where(
                Commitment.id == commitment_id,
                Commitment.workspace_id == account.workspace.id,
                Commitment.user_id == account.user.id,
                CommitmentSource.connection_id.in_(mailboxes),
                CommitmentSource.provider == "google",
                CommitmentSource.source_type == "gmail_message",
                CommitmentSource.external_resource_id.is_not(None),
                CommitmentSource.external_resource_id != "",
                ~known_deleted_source,
            )
            .order_by(CommitmentSource.extracted_at.desc(), CommitmentSource.id)
            .limit(20)
        )
    ).all()
    result = []
    for index, (source_id, connection_id, external_id) in enumerate(rows, start=1):
        mailbox = mailboxes[connection_id]
        message_label = f"Gmail message {index}" if len(rows) > 1 else "Gmail message"
        option = {
            "id": str(source_id),
            "external_id": external_id,
            "provider": "Gmail",
            "connection_id": str(connection_id),
            "label": f"{message_label} · {mailbox}" if mailbox else message_label,
        }
        if mailbox is not None:
            option["account_email"] = mailbox
        result.append(option)
    return result


@router.get("/sources/{commitment_id}/{source_id}/message")
async def read_source_message(
    commitment_id: UUID,
    source_id: UUID,
    response: Response,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict:
    """Fetch exactly the selected, currently authorized message. Never persist its body."""
    import asyncio
    from datetime import UTC, datetime
    from urllib.parse import quote

    from navox.intelligence.ingestion import authorized_connection
    from navox.intelligence.source_cooldown import source_retry_after
    from navox.providers.google_oauth import access_token_for_connection
    from navox.providers.google_sources import GoogleSourceGateway

    response.headers["Cache-Control"] = "private, no-store"
    user_id, workspace_id = account.user.id, account.workspace.id
    sources = await list_sources(commitment_id, account, database)
    selected = next((row for row in sources if row["id"] == str(source_id)), None)
    if selected is None:
        raise HTTPException(404, "This email is no longer available")
    connection_id = UUID(selected["connection_id"])
    from navox.communication.service import authorize_source

    scope = CommunicationDraft(
        id=uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        commitment_id=commitment_id,
        binding_kind="COMMITMENT",
        source_connection_id=connection_id,
        source_reference=str(source_id),
    )
    try:
        await authorize_source(database, scope)
        async with asyncio.timeout(30):
            connection, _ = await authorized_connection(database, connection_id, "gmail")
            if await source_retry_after(database, connection_id, "gmail"):
                raise HTTPException(429, "Gmail is busy. Try again shortly")
            token = await access_token_for_connection(
                database, connection=connection, settings=settings
            )
            await database.commit()
            if selected not in await list_sources(commitment_id, account, database):
                raise HTTPException(409, "This email changed. Open it again")
            await database.rollback()
            document = await GoogleSourceGateway().gmail_message(
                token,
                workspace_id=workspace_id,
                connection_id=connection_id,
                external_id=selected["external_id"],
                now=datetime.now(UTC),
            )
        if (
            document.workspace_id,
            document.provider,
            document.source_type,
            document.external_id,
        ) != (
            workspace_id,
            "google",
            "gmail_message",
            selected["external_id"],
        ) or document.metadata.get("status") == "deleted":
            raise HTTPException(404, "This email is no longer available")
        await database.refresh(account.workspace)
        await database.refresh(account.user)
        await authorize_source(database, scope)
        if selected not in await list_sources(commitment_id, account, database):
            raise HTTPException(409, "This email changed. Open it again")
        content = document.content or ""
        return {
            "subject": document.subject or "(No subject)",
            "sender": document.author.identity_value if document.author else None,
            "body": content[:12000],
            "truncated": len(content) > 12000,
            "sent_at": document.occurred_at.isoformat(),
            "source_id": str(source_id),
            "open_url": "https://mail.google.com/mail/?authuser="
            + quote(selected.get("account_email", ""), safe="")
            + "#all/"
            + quote(selected["external_id"], safe=""),
        }
    except (GoogleSourceError, GoogleAccessTokenError, TimeoutError):
        raise HTTPException(
            503,
            "This email couldn’t load. Try again shortly",
            headers={"Cache-Control": "private, no-store"},
        ) from None
    except ApprovalServiceError as error:
        raise approval_error(error) from None


@router.delete("/{draft_id}")
async def discard_draft(
    draft_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    expected_version: int = Query(ge=1),
) -> dict:
    from navox.db.models import Action, User

    require_origin(request, settings.web_origin)
    try:
        await database.scalar(select(User).where(User.id == account.user.id).with_for_update())
        draft = await owned_draft(
            database, draft_id, user_id=account.user.id, workspace_id=account.workspace.id
        )
        if draft.current_version != expected_version:
            raise HTTPException(409, "This draft changed. Review it again before discarding")
        action_id = draft.action_id
        if action_id:
            action = await database.scalar(
                select(Action).where(Action.id == action_id).with_for_update()
            )
            if action is None or action.status != "awaiting_approval":
                raise HTTPException(409, "Check the send outcome before discarding this draft")
            await ApprovalService().reject(
                database,
                action_id=action_id,
                user_id=account.user.id,
                workspace_id=account.workspace.id,
                request_id=uuid4(),
            )
            await database.scalar(select(User).where(User.id == account.user.id).with_for_update())
        draft = await database.scalar(
            select(CommunicationDraft)
            .where(
                CommunicationDraft.id == draft_id,
                CommunicationDraft.user_id == account.user.id,
                CommunicationDraft.workspace_id == account.workspace.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            draft is None
            or draft.current_version != expected_version
            or draft.action_id != action_id
        ):
            raise HTTPException(409, "This draft changed. Review it again before discarding")
        await database.delete(draft)
        await database.commit()
        return {"discarded": True}
    except ApprovalServiceError as error:
        raise approval_error(error) from None


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
