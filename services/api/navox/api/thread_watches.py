"""Opt-in, owner-bound follow-up watches. Reads never contact Gmail."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.approvals.schemas import StoredGmailSendPayload
from navox.db.models import Action, Commitment, CommitmentSource, WorkspaceMembership
from navox.db.thread_watches import ThreadWatch
from navox.thread_watches.dispatcher import dispatch_thread_watch
from navox.thread_watches.service import (
    TERMINAL,
    WatchAccessError,
    authorize,
    owned_action,
    send_binding,
    utc,
)

router = APIRouter(prefix="/thread-watches", tags=["thread watches"])
WatchStatus = Literal[
    "watching", "due", "replied", "paused", "cancelled", "needs_access", "check_failed"
]


class ReminderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remind_at: AwareDatetime

    @field_validator("remind_at")
    @classmethod
    def future_reminder(cls, value: datetime) -> datetime:
        now = datetime.now(UTC)
        if not now < value <= now + timedelta(days=30):
            raise ValueError("Choose a future reminder within 30 days")
        return value.astimezone(UTC)


class CreateWatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: UUID
    request_id: UUID
    remind_at: AwareDatetime


class ThreadWatchView(BaseModel):
    id: UUID
    action_id: UUID
    connection_id: UUID
    commitment_id: UUID | None
    compose_commitment_id: UUID | None
    sender_email: str
    recipient_email: str
    subject: str
    remind_at: datetime
    status: str
    reason: str | None
    last_checked_at: datetime | None
    next_check_at: datetime | None
    replied_at: datetime | None
    provider_url: str
    created_at: datetime
    updated_at: datetime


class ThreadWatchPage(BaseModel):
    items: list[ThreadWatchView]


def unavailable(code: int, detail: str) -> HTTPException:
    return HTTPException(code, detail, headers={"Cache-Control": "no-store"})


async def require_owner(database: DatabaseSession, account: CurrentAccountDependency) -> None:
    membership = await database.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == account.user.id,
            WorkspaceMembership.workspace_id == account.workspace.id,
            WorkspaceMembership.role == "owner",
        )
    )
    if membership is None or account.workspace.workspace_type != "personal":
        raise unavailable(403, "Thread watches require your personal workspace.")


async def current_watch(
    database: DatabaseSession, account: CurrentAccountDependency, watch_id: UUID
) -> ThreadWatch:
    await require_owner(database, account)
    watch = await database.scalar(
        select(ThreadWatch)
        .where(
            ThreadWatch.id == watch_id,
            ThreadWatch.user_id == account.user.id,
            ThreadWatch.workspace_id == account.workspace.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if watch is None:
        raise unavailable(404, "Thread watch not found.")
    return watch


async def watch_view(database: DatabaseSession, watch: ThreadWatch) -> ThreadWatchView:
    # SQL onupdate timestamps can be expired after an async commit; load them explicitly.
    await database.refresh(watch)
    commitment_id, compose_id = None, None
    action = await database.scalar(
        select(Action).where(
            Action.id == watch.action_id,
            Action.user_id == watch.user_id,
            Action.workspace_id == watch.workspace_id,
        )
    )
    if action is not None and action.commitment_id is not None:
        commitment = await database.scalar(
            select(Commitment).where(
                Commitment.id == action.commitment_id,
                Commitment.user_id == watch.user_id,
                Commitment.workspace_id == watch.workspace_id,
            )
        )
        if commitment is not None:
            commitment_id = commitment.id
            try:
                payload = StoredGmailSendPayload.model_validate(action.payload)
            except ValidationError:
                payload = None
            if (
                payload is not None
                and payload.reply is not None
                and payload.reply.thread_id == watch.thread_id
                and payload.connection_id == watch.connection_id
                and str(payload.sender).casefold() == watch.sender_email
                and str(payload.to).casefold() == watch.recipient_email
                and commitment.status
                not in {
                    "candidate",
                    "completed",
                    "cancelled",
                    "dismissed",
                }
            ):
                source = await database.scalar(
                    select(CommitmentSource.id).where(
                        CommitmentSource.commitment_id == commitment.id,
                        CommitmentSource.connection_id == watch.connection_id,
                        CommitmentSource.provider == "google",
                        CommitmentSource.source_type == "gmail_message",
                        CommitmentSource.external_resource_id == payload.reply.source_message_id,
                    )
                )
                if source is not None:
                    compose_id = commitment.id
    return ThreadWatchView(
        id=watch.id,
        action_id=watch.action_id,
        connection_id=watch.connection_id,
        commitment_id=commitment_id,
        compose_commitment_id=compose_id,
        sender_email=watch.sender_email,
        recipient_email=watch.recipient_email,
        subject=watch.subject,
        remind_at=utc(watch.remind_at),
        status=watch.status,
        reason=watch.reason,
        last_checked_at=utc(watch.last_checked_at) if watch.last_checked_at else None,
        next_check_at=utc(watch.next_check_at) if watch.next_check_at else None,
        replied_at=utc(watch.replied_at) if watch.replied_at else None,
        provider_url=(
            f"https://mail.google.com/mail/u/?authuser={quote(watch.sender_email, safe='')}"
            f"#all/{quote(watch.thread_id, safe='')}"
        ),
        created_at=utc(watch.created_at),
        updated_at=utc(watch.updated_at),
    )


async def dispatch_saved(
    database: DatabaseSession, watch: ThreadWatch, settings: SettingsDependency
) -> ThreadWatch:
    watch_id, revision, next_check = watch.id, watch.revision, watch.next_check_at
    await database.commit()
    try:
        async with asyncio.timeout(5):
            await dispatch_thread_watch(watch_id=watch_id, revision=revision, settings=settings)
    except Exception:
        # The outbox is still present; worker reconciliation retries after an outage.
        await database.execute(
            update(ThreadWatch)
            .where(
                ThreadWatch.id == watch_id,
                ThreadWatch.revision == revision,
                ThreadWatch.status.not_in(TERMINAL),
                ThreadWatch.next_check_at == next_check,
                ThreadWatch.lease_token.is_(None),
            )
            .values(
                status="check_failed",
                reason="dispatch_failed",
                next_check_at=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
        await database.commit()
    saved = await database.get(ThreadWatch, watch_id, populate_existing=True)
    if saved is None:
        raise unavailable(404, "Thread watch not found.")
    return saved


@router.get("", response_model=ThreadWatchPage)
async def list_watches(
    account: CurrentAccountDependency, database: DatabaseSession, response: Response
) -> ThreadWatchPage:
    response.headers["Cache-Control"] = "no-store"
    await require_owner(database, account)
    rows = list(
        await database.scalars(
            select(ThreadWatch)
            .where(
                ThreadWatch.user_id == account.user.id,
                ThreadWatch.workspace_id == account.workspace.id,
            )
            .order_by(ThreadWatch.created_at.desc(), ThreadWatch.id)
            .limit(200)
        )
    )
    return ThreadWatchPage(items=[await watch_view(database, row) for row in rows])


@router.get("/{watch_id}", response_model=ThreadWatchView)
async def get_watch(
    watch_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
) -> ThreadWatchView:
    response.headers["Cache-Control"] = "no-store"
    return await watch_view(database, await current_watch(database, account, watch_id))


@router.post("", response_model=ThreadWatchView)
async def create_watch(
    payload: CreateWatchRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> ThreadWatchView:
    response.headers["Cache-Control"] = "no-store"
    await require_owner(database, account)
    existing = await database.scalar(
        select(ThreadWatch).where(
            ThreadWatch.user_id == account.user.id,
            ThreadWatch.workspace_id == account.workspace.id,
            ThreadWatch.request_id == payload.request_id,
        )
    )
    if existing is not None:
        if existing.action_id != payload.action_id:
            raise unavailable(409, "This request already belongs to another sent email.")
        return await watch_view(database, existing)
    existing = await database.scalar(
        select(ThreadWatch).where(
            ThreadWatch.user_id == account.user.id,
            ThreadWatch.workspace_id == account.workspace.id,
            ThreadWatch.action_id == payload.action_id,
        )
    )
    if existing is not None:
        return await watch_view(database, existing)
    try:
        ReminderRequest(remind_at=payload.remind_at)
    except ValidationError:
        raise unavailable(422, "Choose a future reminder within 30 days.") from None
    try:
        action = await owned_action(
            database,
            action_id=payload.action_id,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
        )
        binding = send_binding(action)
        await authorize(
            database,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
            connection_id=binding.connection_id,
            sender=binding.sender,
            lock=True,
        )
    except WatchAccessError as error:
        if error.reason == "action_not_found":
            raise unavailable(404, "Sent email not found.") from None
        if error.reason in {"send_not_verified", "send_reference_unavailable"}:
            raise unavailable(
                409, "Only a verified sent email with a Gmail thread can be watched."
            ) from None
        raise unavailable(
            403, "Resume NavoX and enable Gmail read access to watch replies."
        ) from None
    watch = ThreadWatch(
        user_id=account.user.id,
        workspace_id=account.workspace.id,
        action_id=action.id,
        connection_id=binding.connection_id,
        request_id=payload.request_id,
        action_payload_hash=binding.payload_hash,
        sender_email=binding.sender,
        recipient_email=binding.recipient,
        subject=binding.subject,
        sent_message_id=binding.message_id,
        thread_id=binding.thread_id,
        remind_at=payload.remind_at,
        next_check_at=datetime.now(UTC),
        status="watching",
    )
    database.add(watch)
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        existing = await database.scalar(
            select(ThreadWatch).where(
                ThreadWatch.user_id == account.user.id,
                ThreadWatch.workspace_id == account.workspace.id,
                ThreadWatch.action_id == payload.action_id,
            )
        )
        if existing is None:
            raise unavailable(409, "This watch request was already used.") from None
        return await watch_view(database, existing)
    return await watch_view(database, await dispatch_saved(database, watch, settings))


@router.patch("/{watch_id}", response_model=ThreadWatchView)
async def reschedule_watch(
    watch_id: UUID,
    payload: ReminderRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> ThreadWatchView:
    response.headers["Cache-Control"] = "no-store"
    watch = await current_watch(database, account, watch_id)
    if watch.status in {"cancelled", "replied"}:
        raise unavailable(409, "This watch has finished.")
    paused = watch.status == "paused"
    changed = await database.scalar(
        update(ThreadWatch)
        .where(
            ThreadWatch.id == watch.id,
            ThreadWatch.revision == watch.revision,
            ThreadWatch.status.not_in({"cancelled", "replied"}),
        )
        .values(
            remind_at=payload.remind_at,
            revision=ThreadWatch.revision + 1,
            lease_token=None,
            lease_until=None,
            reason=None,
            status="paused" if paused else "watching",
            next_check_at=None if paused else datetime.now(UTC),
        )
        .returning(ThreadWatch.id)
        .execution_options(synchronize_session=False)
    )
    await database.commit()
    watch = await current_watch(database, account, watch_id)
    if changed is not None and not paused:
        watch = await dispatch_saved(database, watch, settings)
    return await watch_view(database, watch)


@router.post("/{watch_id}/{operation}", response_model=ThreadWatchView)
async def control_watch(
    watch_id: UUID,
    operation: Literal["pause", "resume", "cancel", "check"],
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> ThreadWatchView:
    response.headers["Cache-Control"] = "no-store"
    watch = await current_watch(database, account, watch_id)
    if watch.status in {"cancelled", "replied"}:
        return await watch_view(database, watch)
    if operation == "check" and watch.status == "paused":
        raise unavailable(409, "Resume this watch before checking Gmail.")
    if operation == "resume" and watch.status != "paused":
        return await watch_view(database, watch)
    if operation == "check":
        now = datetime.now(UTC)
        if (
            (watch.lease_until is not None and utc(watch.lease_until) > now)
            or (
                watch.last_checked_at is not None
                and utc(watch.last_checked_at) > now - timedelta(minutes=1)
            )
            or (
                watch.next_check_at is not None
                and utc(watch.next_check_at) <= now
                and utc(watch.updated_at) > now - timedelta(minutes=1)
            )
        ):
            return await watch_view(database, watch)
    stopping = operation in {"pause", "cancel"}
    if operation == "pause":
        next_status = "paused"
    elif operation == "cancel":
        next_status = "cancelled"
    elif operation == "check" and watch.status == "due":
        next_status = "due"
    else:
        next_status = "watching"
    changed = await database.scalar(
        update(ThreadWatch)
        .where(
            ThreadWatch.id == watch.id,
            ThreadWatch.revision == watch.revision,
            ThreadWatch.status.not_in({"cancelled", "replied"}),
        )
        .values(
            revision=ThreadWatch.revision + 1,
            lease_token=None,
            lease_until=None,
            reason=watch.reason if next_status == "due" else None,
            status=next_status,
            next_check_at=None if stopping else datetime.now(UTC),
        )
        .returning(ThreadWatch.id)
        .execution_options(synchronize_session=False)
    )
    await database.commit()
    watch = await current_watch(database, account, watch_id)
    if changed is not None and not stopping:
        watch = await dispatch_saved(database, watch, settings)
    return await watch_view(database, watch)
