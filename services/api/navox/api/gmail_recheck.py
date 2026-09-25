"""Owner-requested previews and selected cleanup of older Gmail-derived cards."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.ai.errors import AIProviderError
from navox.ai.factory import AIProviderNotConfigured, build_ai_gateway
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.intelligence_evidence import readable_connection, unavailable, verified_excerpts
from navox.db.models import (
    Commitment,
    CommitmentSource,
    Connection,
    GmailRecheck,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence.email_relevance import filter_email_extraction
from navox.intelligence.extraction import (
    INSTRUCTION_LIKE_MARKERS,
    InvalidOperationalExtraction,
    OperationalExtractor,
    OperationalObservationCandidate,
)
from navox.intelligence.gmail_recheck import (
    ACTIVE_STATUSES,
    POLICY_VERSION,
    CardSnapshot,
    assess_card_support,
    snapshot_card,
    utc,
)
from navox.intelligence.source_cooldown import SOURCE_BACKOFF_CODES, source_retry_after
from navox.providers.google_oauth import GoogleAccessTokenError, access_token_for_connection
from navox.providers.google_sources import GoogleSourceError, GoogleSourceGateway

router = APIRouter(prefix="/intelligence/gmail-recheck", tags=["intelligence"])
PREVIEW_TTL = timedelta(minutes=30)
PAGE_SIZE = 25


class RecheckScope(TypedDict):
    connection_id: UUID
    user_id: UUID
    workspace_id: UUID


class RecheckItem(BaseModel):
    commitment_id: UUID
    title: str
    outcome: str
    reason: str
    preview_id: UUID | None = None
    checked_at: datetime | None = None
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=3)


class RecheckPage(BaseModel):
    items: list[RecheckItem]
    next_after: UUID | None


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    commitment_id: UUID
    retry: bool = False


class SelectedPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commitment_id: UUID
    preview_id: UUID


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    action: Literal["remove", "keep"]
    items: list[SelectedPreview] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def distinct_items(self) -> "ApplyRequest":
        if len({item.commitment_id for item in self.items}) != len(self.items):
            raise ValueError("Select each item once")
        return self


class ApplyResponse(BaseModel):
    applied: list[UUID]
    skipped: list[UUID]


async def authorize(
    database: AsyncSession,
    *,
    connection_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    lock: bool = False,
) -> Connection:
    statement = (
        select(Connection)
        .where(
            Connection.id == connection_id,
            Connection.user_id == user_id,
            Connection.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(key_share=True)
    if await database.scalar(statement) is None:
        raise unavailable(404, "Google connection not found.")
    if lock:
        # Serialize the short claim/apply transaction with pause or membership removal.
        # Follow feedback's workspace-before-card order; permit foreign-key key-share locks.
        await database.scalar(
            select(Workspace).where(Workspace.id == workspace_id).with_for_update(key_share=True)
        )
        await database.scalar(
            select(User).where(User.id == user_id).with_for_update(key_share=True)
        )
        await database.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == user_id,
            )
            .with_for_update(key_share=True)
        )
    return await readable_connection(
        database,
        connection_id=connection_id,
        user_id=user_id,
        workspace_id=workspace_id,
    )


async def load_card(
    database: AsyncSession,
    *,
    commitment_id: UUID,
    connection_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    lock: bool = False,
) -> Commitment:
    statement = (
        select(Commitment)
        .where(
            Commitment.id == commitment_id,
            Commitment.user_id == user_id,
            Commitment.workspace_id == workspace_id,
            select(CommitmentSource.id)
            .where(
                CommitmentSource.commitment_id == Commitment.id,
                CommitmentSource.connection_id == connection_id,
            )
            .exists(),
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(key_share=True)
    commitment = await database.scalar(statement)
    if commitment is None:
        raise unavailable(404, "Gmail item not found.")
    return commitment


def item_view(
    card: Commitment,
    snapshot: CardSnapshot,
    saved: GmailRecheck | None,
) -> RecheckItem:
    item = RecheckItem(
        commitment_id=card.id,
        title=card.title,
        outcome="unchecked",
        reason="not_checked",
        evidence_ids=list({e.external_resource_id: e.id for e in snapshot.evidence}.values())[:3],
    )
    if snapshot.reason is not None:
        return item.model_copy(update={"outcome": "needs_review", "reason": snapshot.reason})
    if (
        saved is not None
        and saved.snapshot_hash == snapshot.digest
        and saved.policy_version == POLICY_VERSION
        and utc(saved.expires_at) > datetime.now(UTC)
    ):
        return item.model_copy(
            update={
                "outcome": saved.outcome,
                "reason": saved.reason,
                "preview_id": saved.preview_id,
                "checked_at": utc(saved.checked_at) if saved.checked_at else None,
            }
        )
    return item


@router.get("", response_model=RecheckPage)
async def list_older_items(
    connection_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
    after: UUID | None = None,
) -> RecheckPage:
    response.headers["Cache-Control"] = "no-store"
    user_id, workspace_id = current_account.user.id, current_account.workspace.id
    await authorize(
        database, connection_id=connection_id, user_id=user_id, workspace_id=workspace_id
    )
    statement = select(Commitment).where(
        Commitment.user_id == user_id,
        Commitment.workspace_id == workspace_id,
        Commitment.created_by == "ai",
        Commitment.status.in_(ACTIVE_STATUSES),
        select(CommitmentSource.id)
        .where(
            CommitmentSource.commitment_id == Commitment.id,
            CommitmentSource.connection_id == connection_id,
            CommitmentSource.source_type == "gmail_message",
        )
        .exists(),
    )
    if after is not None:
        statement = statement.where(Commitment.id > after)
    cards = list(await database.scalars(statement.order_by(Commitment.id).limit(PAGE_SIZE + 1)))
    items = []
    for card in cards[:PAGE_SIZE]:
        snapshot = await snapshot_card(database, card, connection_id)
        if snapshot.reason in {"user_decision_or_inactive", "already_current_policy"}:
            continue
        saved = await database.get(GmailRecheck, card.id)
        items.append(item_view(card, snapshot, saved))
    return RecheckPage(
        items=items,
        next_after=cards[PAGE_SIZE - 1].id if len(cards) > PAGE_SIZE else None,
    )


@router.post("/preview", response_model=RecheckItem)
async def preview_older_item(
    payload: PreviewRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
    settings: SettingsDependency,
) -> RecheckItem:
    response.headers["Cache-Control"] = "no-store"
    user_id, workspace_id = current_account.user.id, current_account.workspace.id
    scope: RecheckScope = dict(
        connection_id=payload.connection_id, user_id=user_id, workspace_id=workspace_id
    )
    connection = await authorize(database, **scope, lock=True)
    card = await load_card(database, commitment_id=payload.commitment_id, **scope, lock=True)
    snapshot = await snapshot_card(database, card, connection.id)
    saved = await database.get(GmailRecheck, card.id, populate_existing=True)
    view = item_view(card, snapshot, saved)
    if snapshot.reason is not None:
        return view
    if view.outcome == "checking" or (view.outcome != "unchecked" and not payload.retry):
        return view
    # Connection lock serializes claims only; it is released before slow I/O.
    now = datetime.now(UTC)
    running = await database.scalar(
        select(GmailRecheck.commitment_id)
        .where(
            GmailRecheck.connection_id == connection.id,
            GmailRecheck.outcome == "checking",
            GmailRecheck.expires_at > now,
        )
        .limit(1)
    )
    if running is not None:
        raise unavailable(
            409, "A Gmail recheck is already running. Refresh results before retrying."
        )
    if await source_retry_after(database, connection.id, "gmail"):
        raise unavailable(429, "Gmail is temporarily rate limited. Try again later.")
    try:
        extractor = OperationalExtractor(build_ai_gateway(settings))
    except AIProviderNotConfigured:
        raise unavailable(503, "Configure the AI provider before rechecking Gmail items.") from None
    preview_id = uuid4()
    budget = settings.openai_read_timeout_seconds + 90
    if saved is None:
        saved = GmailRecheck(commitment_id=card.id, connection_id=connection.id)
        database.add(saved)
    saved.preview_id = preview_id
    saved.snapshot_hash = snapshot.digest
    saved.policy_version = POLICY_VERSION
    saved.outcome, saved.reason = "checking", "checking_sources"
    saved.expires_at = now + timedelta(seconds=budget + 30)
    saved.checked_at = None
    await database.commit()

    outcome, reason = "remove_suggested", "no_action_found"
    observations: list[OperationalObservationCandidate] = []
    provider_failure: GoogleSourceError | None = None
    try:
        async with asyncio.timeout(budget):
            # The message itself is retrieved only after this explicit owner request.
            async with asyncio.timeout(30):
                connection = await authorize(database, **scope)
                token = await access_token_for_connection(
                    database, connection=connection, settings=settings
                )
                await database.commit()  # A narrowed refresh grant must persist.
                await authorize(database, **scope)
            gateway = GoogleSourceGateway()
            resource_ids = sorted({e.external_resource_id for e in snapshot.evidence})
            for external_id in resource_ids:
                connection = await authorize(database, **scope)
                if await source_retry_after(database, connection.id, "gmail"):
                    raise unavailable(429, "Gmail is temporarily rate limited. Try again later.")
                owner_email = connection.external_email
                await database.commit()
                async with asyncio.timeout(30):
                    document = await gateway.gmail_message(
                        token,
                        workspace_id=workspace_id,
                        connection_id=payload.connection_id,
                        external_id=external_id,
                        now=datetime.now(UTC),
                    )
                await authorize(database, **scope)
                if (
                    document.workspace_id != workspace_id
                    or document.provider != "google"
                    or document.source_type != "gmail_message"
                    or document.external_id != external_id
                    or document.metadata.get("status") == "deleted"
                ):
                    outcome, reason = "needs_review", "source_unavailable"
                    break
                for evidence in snapshot.evidence:
                    if evidence.external_resource_id == external_id:
                        verified_excerpts(evidence, document)
                text = f"{document.subject or ''}\n{document.content or ''}".casefold()
                if any(marker in text for marker in INSTRUCTION_LIKE_MARKERS):
                    outcome, reason = "needs_review", "untrusted_source"
                    break
                await database.commit()
                result = await extractor.extract(document, owner_email=owner_email)
                await authorize(database, **scope)
                observations.extend(
                    filter_email_extraction(result.extraction, document).observations
                )
            else:
                # Check all support: a later email can describe a status change even
                # when the first email still contains the original request.
                outcome, reason = assess_card_support(card.title, observations)
    except TimeoutError:
        outcome, reason = "failed", "timeout"
    except GoogleAccessTokenError:
        outcome, reason = "failed", "google_access_failed"
    except GoogleSourceError as error:
        provider_failure = error
        outcome, reason = "failed", "google_read_failed"
    except InvalidOperationalExtraction:
        outcome, reason = "needs_review", "invalid_extraction"
    except AIProviderError:
        outcome, reason = "failed", "ai_request_failed"
    except Exception as error:
        # Expected HTTP failures include changed evidence and concurrent revocation.
        # Do not persist or return arbitrary exception messages or provider content.
        from fastapi import HTTPException

        if isinstance(error, HTTPException) and error.status_code in {403, 404, 429}:
            raise
        outcome, reason = (
            "needs_review",
            "source_changed" if isinstance(error, HTTPException) else "check_unavailable",
        )

    connection = await authorize(database, **scope, lock=True)
    card = await load_card(database, commitment_id=payload.commitment_id, **scope, lock=True)
    latest = await snapshot_card(database, card, connection.id)
    saved = await database.get(GmailRecheck, card.id, populate_existing=True)
    if saved is None or saved.preview_id != preview_id or saved.outcome != "checking":
        raise unavailable(409, "This recheck was replaced. Refresh results.")
    if latest.digest != snapshot.digest or latest.reason is not None:
        outcome, reason = "needs_review", "item_changed"
    saved.outcome, saved.reason = outcome, reason
    saved.checked_at = datetime.now(UTC)
    saved.expires_at = saved.checked_at + PREVIEW_TTL
    add_audit_event(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        event_type="intelligence.gmail_recheck.previewed",
        entity_type="commitment",
        entity_id=card.id,
        metadata={"outcome": outcome, "reason": reason, "policy": POLICY_VERSION},
    )
    if provider_failure is not None and provider_failure.code in SOURCE_BACKOFF_CODES:
        retry = max(provider_failure.retry_after_seconds or 0, 60)
        if provider_failure.code in {"google_daily_limit_exceeded", "google_quota_exceeded"}:
            retry = max(retry, 300)
        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="intelligence.source.failed",
            entity_type="connection",
            entity_id=connection.id,
            metadata={
                "source": "gmail",
                "error_type": "GoogleSourceError",
                "error_diagnostic": provider_failure.diagnostic(),
                "retry_not_before": (
                    datetime.now(UTC) + timedelta(seconds=min(retry, 86400))
                ).isoformat(),
            },
        )
    await database.commit()
    return RecheckItem(
        commitment_id=card.id,
        title=card.title,
        outcome=outcome,
        reason=reason,
        preview_id=preview_id,
        checked_at=saved.checked_at,
        evidence_ids=list({e.external_resource_id: e.id for e in snapshot.evidence}.values())[:3],
    )


@router.post("/apply", response_model=ApplyResponse)
async def apply_selected_previews(
    payload: ApplyRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
) -> ApplyResponse:
    response.headers["Cache-Control"] = "no-store"
    user_id, workspace_id = current_account.user.id, current_account.workspace.id
    scope: RecheckScope = dict(
        connection_id=payload.connection_id, user_id=user_id, workspace_id=workspace_id
    )
    await authorize(database, **scope, lock=True)
    applied: list[UUID] = []
    skipped: list[UUID] = []
    # Load every selected item in owner scope before making any change.
    cards = {
        selected.commitment_id: await load_card(
            database,
            commitment_id=selected.commitment_id,
            **scope,
            lock=True,
        )
        for selected in sorted(payload.items, key=lambda item: str(item.commitment_id))
    }
    for selected in payload.items:
        card = cards[selected.commitment_id]
        saved = await database.get(GmailRecheck, card.id, populate_existing=True)
        target = "removed" if payload.action == "remove" else "kept"
        if (
            saved is not None
            and saved.preview_id == selected.preview_id
            and saved.connection_id == payload.connection_id
            and saved.outcome == target
            and (
                card.status == "rejected" if target == "removed" else card.status in ACTIVE_STATUSES
            )
        ):
            applied.append(card.id)  # Lost-response retries do not create a second audit.
            continue
        snapshot = await snapshot_card(database, card, payload.connection_id)
        if (
            saved is None
            or saved.preview_id != selected.preview_id
            or saved.connection_id != payload.connection_id
            or saved.policy_version != POLICY_VERSION
            or utc(saved.expires_at) <= datetime.now(UTC)
            or saved.snapshot_hash != snapshot.digest
            or snapshot.reason is not None
            or saved.outcome not in {"remove_suggested", "retained", "needs_review", "failed"}
            or (payload.action == "remove" and saved.outcome != "remove_suggested")
        ):
            skipped.append(card.id)
            continue
        if payload.action == "remove":
            card.status = "rejected"
        saved.outcome, saved.reason = target, "owner_selected"
        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="commitment.dismissed" if target == "removed" else "commitment.kept",
            entity_type="commitment",
            entity_id=card.id,
            actor_type="user",
            actor_id=str(user_id),
            metadata={"reason": "gmail_recheck", "preview_id": str(saved.preview_id)},
        )
        applied.append(card.id)
    await database.commit()
    return ApplyResponse(applied=applied, skipped=skipped)
