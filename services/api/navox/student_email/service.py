"""Explicit email review and task proposals; bodies are never retained or indexed."""

import asyncio
import json
import re
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.builtin.google import GOOGLE_CONNECTOR_KEY, google_canonical_resource
from navox.connectors.builtin.google_gmail import GMAIL_MANIFEST, gmail_resource
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.import_storage import cipher
from navox.connectors.runtime import resource_hash
from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.coursework import service as coursework
from navox.db.models import (
    Commitment,
    CommitmentSource,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    User,
)
from navox.db.student import StudentCourse
from navox.db.student_email import EmailTaskSource, StudentEmailReview
from navox.db.student_planning import StudentStudyBlock, StudentTask
from navox.intelligence.attention import ACTIVE
from navox.intelligence.extraction import source_document_hash
from navox.student import service as schedule
from navox.student_email.classifier import classify
from navox.student_email.reader import EmailReader
from navox.student_email.schemas import AcceptInput, AnalyzeInput, CorrectionInput
from navox.student_planning import service as planning
from navox.student_planning.schemas import MAX_TASKS

TTL = timedelta(minutes=20)
RETRY_WINDOW = timedelta(days=7)
MAX_RECEIPTS = 1000
MAX_ACTIVE = 20
ANALYZE_SLOTS = asyncio.Semaphore(2)


class EmailError(ValueError):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


digest = coursework.digest


async def verified_source(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    source_id: UUID,
    source_hash: str,
) -> tuple[ConnectorResource, ConnectorConnection, str]:
    user = await schedule.owner(database, user_id, workspace_id)
    if user.agent_paused:
        raise EmailError("Resume NavoX before reviewing email", 403)
    resource = await coursework.email_source(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        source_id=source_id,
        source_hash=source_hash,
    )
    connector = await database.get(
        ConnectorConnection, resource.connector_connection_id, populate_existing=True
    )
    if connector is None:
        raise EmailError("The selected email connection is unavailable", 403)
    definition = await database.get(ConnectorDefinition, connector.connector_definition_id)
    if (
        resource.provider != "google"
        or connector.legacy_connection_id is None
        or definition is None
        or not definition.active
        or definition.connector_class != "OAUTH_API"
        or definition.trust_level != "NAVOX_FIRST_PARTY"
        or definition.connector_key not in {GMAIL_MANIFEST.id, GOOGLE_CONNECTOR_KEY}
        or not re.fullmatch(r"[a-fA-F0-9]{8,128}", resource.external_id)
    ):
        raise EmailError("Select a current message from your authorized Gmail source", 403)
    legacy = await database.get(Connection, connector.legacy_connection_id, populate_existing=True)
    if legacy is None or legacy.provider != "google":
        raise EmailError("The selected Gmail source is unavailable", 403)
    return resource, connector, definition.connector_key


async def source_authority_hash(
    database: AsyncSession, connector: ConnectorConnection, *, in_flight: bool = True
) -> str:
    legacy = await database.get(Connection, connector.legacy_connection_id, populate_existing=True)
    if legacy is None:
        raise EmailError("The selected email authority changed", 403)
    return digest(
        {
            "connector_id": str(connector.id),
            "definition_id": str(connector.connector_definition_id),
            "generation": connector.sync_generation if in_flight else None,
            "legacy_id": str(connector.legacy_connection_id),
            "account": connector.external_account_id,
            "status": connector.status if in_flight else None,
            "health": connector.health_state if in_flight else None,
            "authorized": sorted(connector.authorized_capabilities),
            "provided": sorted(connector.provider_capabilities),
            "credential": str(legacy.credential_reference),
            "legacy_account": legacy.external_account_id,
            "legacy_scopes": sorted(legacy.granted_scopes),
            "legacy_status": legacy.status,
        }
    )


def seal(row: StudentEmailReview, payload: dict[str, Any], settings: Settings) -> str:
    envelope = {
        "version": 1,
        "id": str(row.id),
        "owner": [str(row.user_id), str(row.workspace_id)],
        "request_id": str(row.request_id),
        "payload_hash": row.payload_hash,
        "source_id": str(row.source_id),
        "connector_id": str(row.connector_id),
        "source_hash": row.source_hash,
        "authority_hash": row.authority_hash,
        "revision": row.revision,
        "expires_at": utc(row.expires_at).isoformat(),
        "payload": payload,
    }
    try:
        return (
            cipher(settings).encrypt(json.dumps(envelope, separators=(",", ":")).encode()).decode()
        )
    except ConnectorRuntimeError:
        raise EmailError("Private email review encryption is not configured", 503) from None


def reveal(row: StudentEmailReview, settings: Settings) -> dict[str, Any]:
    if row.encrypted_payload is None:
        raise EmailError(
            "This email review was erased or is unavailable. Analyze a new request", 410
        )
    try:
        value = json.loads(cipher(settings).decrypt(row.encrypted_payload.encode()))
        expected = {
            "version": 1,
            "id": str(row.id),
            "owner": [str(row.user_id), str(row.workspace_id)],
            "request_id": str(row.request_id),
            "payload_hash": row.payload_hash,
            "source_id": str(row.source_id),
            "connector_id": str(row.connector_id),
            "source_hash": row.source_hash,
            "authority_hash": row.authority_hash,
            "revision": row.revision,
            "expires_at": utc(row.expires_at).isoformat(),
        }
        if {key: value[key] for key in expected} != expected or not isinstance(
            value["payload"], dict
        ):
            raise ValueError
        return cast(dict[str, Any], value["payload"])
    except (InvalidToken, ConnectorRuntimeError, ValueError, KeyError, TypeError, UnicodeError):
        raise EmailError("This private email review cannot be decrypted", 503) from None


async def owned_review(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, review_id: UUID
) -> StudentEmailReview:
    row = await database.scalar(
        select(StudentEmailReview)
        .where(
            StudentEmailReview.id == review_id,
            StudentEmailReview.user_id == user_id,
            StudentEmailReview.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise EmailError("Email review not found", 404)
    return row


async def current_review(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    review_id: UUID,
) -> tuple[StudentEmailReview, dict[str, Any]]:
    await schedule.owner(database, user_id, workspace_id)
    row = await owned_review(
        database, user_id=user_id, workspace_id=workspace_id, review_id=review_id
    )
    if utc(row.expires_at) <= await database_now(database):
        raise EmailError("This email review expired. Analyze the selected message again", 410)
    if row.source_id is None or row.source_hash is None or row.connector_id is None:
        raise EmailError("This email source is unavailable", 410)
    _, connector, _ = await verified_source(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        source_id=row.source_id,
        source_hash=row.source_hash,
    )
    if connector.id != row.connector_id or row.authority_hash != await source_authority_hash(
        database, connector
    ):
        raise EmailError("The email read authority changed. Analyze a new review")
    payload = reveal(row, settings)
    return row, payload


async def reply_handoff(
    database: AsyncSession,
    source: ConnectorResource,
    connector: ConnectorConnection,
    *,
    user_id: UUID,
    workspace_id: UUID,
) -> dict[str, str] | None:
    row = (
        await database.execute(
            select(Commitment.id, CommitmentSource.id)
            .join(CommitmentSource, CommitmentSource.commitment_id == Commitment.id)
            .where(
                Commitment.user_id == user_id,
                Commitment.workspace_id == workspace_id,
                Commitment.status.in_(ACTIVE),
                CommitmentSource.provider == "google",
                CommitmentSource.source_type == "gmail_message",
                CommitmentSource.connection_id == connector.legacy_connection_id,
                CommitmentSource.external_resource_id == source.external_id,
            )
            .order_by(Commitment.updated_at.desc(), CommitmentSource.id)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return {
        "commitment_id": str(row[0]),
        "source_id": str(row[1]),
        "path": f"/inbox?reply={row[0]}#drafts",
        "notice": "Use the existing draft review flow. "
        "Review the exact recipient, subject and body before any approval or send.",
    }


async def view(
    database: AsyncSession,
    row: StudentEmailReview,
    payload: dict[str, Any],
    *,
    user_id: UUID,
    workspace_id: UUID,
) -> dict[str, Any]:
    source, connector, _ = await verified_source(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        source_id=cast(UUID, row.source_id),
        source_hash=cast(str, row.source_hash),
    )
    if row.authority_hash != await source_authority_hash(database, connector):
        raise EmailError("The email read authority changed. Analyze a new review")
    existing_task = await database.scalar(
        select(StudentTask)
        .join(EmailTaskSource, EmailTaskSource.task_id == StudentTask.id)
        .where(
            StudentTask.user_id == user_id,
            StudentTask.workspace_id == workspace_id,
            StudentTask.source_identity == digest({"student_email_source": str(row.source_id)}),
            EmailTaskSource.user_id == user_id,
            EmailTaskSource.workspace_id == workspace_id,
        )
        .limit(1)
    )
    return {
        "review_id": str(row.id),
        "revision": row.revision,
        "source_id": str(row.source_id),
        "source_hash": row.source_hash,
        "expires_at": utc(row.expires_at).isoformat(),
        **payload,
        "reply_handoff": await reply_handoff(
            database, source, connector, user_id=user_id, workspace_id=workspace_id
        ),
        "actions_executed": False,
        "task_id": str(row.accepted_task_id) if row.accepted_task_id else None,
        "existing_task": {
            "id": str(existing_task.id),
            "revision": existing_task.revision,
            "title": existing_task.title,
            "course_id": str(existing_task.course_id) if existing_task.course_id else None,
            "status": existing_task.status,
            "remaining_minutes": existing_task.remaining_minutes,
            "actual_minutes": existing_task.actual_minutes,
        }
        if existing_task
        else None,
        "notice": "This is a conservative review proposal, not a verified sender or course. "
        "Your corrections affect this review only. No email or calendar event was sent.",
    }


async def read_review(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    review_id: UUID,
) -> dict[str, Any]:
    row, payload = await current_review(
        database, settings, user_id=user_id, workspace_id=workspace_id, review_id=review_id
    )
    return await view(database, row, payload, user_id=user_id, workspace_id=workspace_id)


async def list_sources(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, query: str
) -> dict[str, object]:
    await schedule.owner(database, user_id, workspace_id)
    query = query.strip()
    if not 2 <= len(query) <= 120 or any(ord(char) < 32 for char in query):
        raise EmailError("Search saved items using 2 to 120 characters", 422)
    rows = list(
        await database.scalars(
            select(ConnectorResource)
            .join(
                ConnectorConnection,
                ConnectorConnection.id == ConnectorResource.connector_connection_id,
            )
            .join(
                ConnectorDefinition,
                ConnectorDefinition.id == ConnectorConnection.connector_definition_id,
            )
            .where(
                ConnectorResource.workspace_id == workspace_id,
                ConnectorResource.resource_type == "communication.message",
                ConnectorResource.deleted.is_(False),
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorDefinition.connector_key.in_([GMAIL_MANIFEST.id, GOOGLE_CONNECTOR_KEY]),
                ConnectorDefinition.trust_level == "NAVOX_FIRST_PARTY",
            )
            .order_by(ConnectorResource.retrieved_at.desc(), ConnectorResource.id)
            .limit(201)
        )
    )
    items = []
    for source in rows[:200]:
        try:
            await verified_source(
                database,
                user_id=user_id,
                workspace_id=workspace_id,
                source_id=source.id,
                source_hash=source.content_hash,
            )
        except (EmailError, coursework.CourseworkError):
            continue
        label = await coursework.email_saved_label(
            database, source, user_id=user_id, workspace_id=workspace_id
        )
        title, sender = coursework.email_labels(source)
        if not all(
            term in " ".join(value for value in (label, title, sender) if value).casefold()
            for term in query.casefold().split()
        ):
            continue
        items.append(
            {
                "source_id": str(source.id),
                "source_hash": source.content_hash,
                "display_label": label,
                "title": title,
                "sender_label": sender,
            }
        )
        if len(items) == 20:
            break
    return {
        "sources": items,
        "limit_reached": len(rows) > 200 or len(items) == 20,
        "notice": "Search checks saved labels only. Analyze reads exactly the selected message; "
        "it does not read attachments. No mailbox content was fetched by this search.",
    }


async def analyze(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    payload: AnalyzeInput,
    reader: EmailReader,
) -> dict[str, Any]:
    if ANALYZE_SLOTS.locked():
        raise EmailError("Two email reviews are already running. Try shortly", 429)
    async with ANALYZE_SLOTS:
        user = await schedule.owner(database, user_id, workspace_id, lock=True)
        body_hash = digest(payload.model_dump(mode="json"))
        existing = await database.scalar(
            select(StudentEmailReview).where(
                StudentEmailReview.user_id == user_id,
                StudentEmailReview.workspace_id == workspace_id,
                StudentEmailReview.request_id == payload.request_id,
            )
        )
        if existing is not None:
            if existing.payload_hash != body_hash:
                raise EmailError("This request identifier was already used for another review")
            return await read_review(
                database,
                settings,
                user_id=user_id,
                workspace_id=workspace_id,
                review_id=existing.id,
            )
        source, connector, definition_key = await verified_source(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            source_id=payload.source_id,
            source_hash=payload.source_hash,
        )
        authority_hash = await source_authority_hash(database, connector)
        now = await database_now(database)
        count = await database.scalar(
            select(func.count())
            .select_from(StudentEmailReview)
            .where(
                StudentEmailReview.user_id == user_id,
                StudentEmailReview.workspace_id == workspace_id,
            )
        )
        active = await database.scalar(
            select(func.count())
            .select_from(StudentEmailReview)
            .where(
                StudentEmailReview.user_id == user_id,
                StudentEmailReview.workspace_id == workspace_id,
                StudentEmailReview.source_id.is_not(None),
                StudentEmailReview.expires_at > now,
            )
        )
        if (count or 0) >= MAX_RECEIPTS:
            raise EmailError("The seven-day email review request limit was reached. Try later", 429)
        if (active or 0) >= MAX_ACTIVE:
            raise EmailError("The active email review limit was reached. Forget older reviews", 429)
        # Validate encryption before reading any private provider content.
        row = StudentEmailReview(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=payload.request_id,
            payload_hash=body_hash,
            source_id=payload.source_id,
            source_hash=payload.source_hash,
            authority_hash=authority_hash,
            connector_id=connector.id,
            revision=0,
            expires_at=now + TTL,
            purge_after=now + RETRY_WINDOW,
        )
        seal(row, {}, settings)
        source_meta = (source.external_id, connector.id, cast(UUID, connector.legacy_connection_id))
        timezone = user.timezone
        review_id = row.id
        database.add(row)
        await database.commit()  # Reserve a content-free request before slow external reads.
        try:
            async with asyncio.timeout(35):
                document = await reader.read(
                    database,
                    settings,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    connector_id=source_meta[1],
                    legacy_id=source_meta[2],
                    external_id=source_meta[0],
                    source_id=payload.source_id,
                    source_hash=payload.source_hash,
                )
        except TimeoutError:
            raise EmailError(
                "The selected email read timed out. Use a new analysis request", 504
            ) from None
        except (EmailError, coursework.CourseworkError, schedule.StudentError):
            raise
        except Exception:
            raise EmailError(
                "The selected email could not be read. Reconnect Gmail or try later", 503
            ) from None
        await schedule.owner(database, user_id, workspace_id, lock=True)
        source, connector, fresh_key = await verified_source(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            source_id=payload.source_id,
            source_hash=payload.source_hash,
        )
        if authority_hash != await source_authority_hash(database, connector):
            raise EmailError(
                "The email read authority changed while the message was read. Analyze again"
            )
        if (
            document.workspace_id != workspace_id
            or document.provider != "google"
            or document.source_type != "gmail_message"
            or document.external_id != source_meta[0]
            or document.metadata.get("status") == "deleted"
            or fresh_key != definition_key
        ):
            raise EmailError("The selected email changed or is unavailable. Sync and review again")
        canonical = (
            gmail_resource(document, connector.id)
            if definition_key == GMAIL_MANIFEST.id
            else google_canonical_resource(document, connector_connection_id=connector.id)
        )
        if resource_hash(canonical) != payload.source_hash:
            raise EmailError("The email changed since it was saved. Sync Gmail and analyze again")
        row = await owned_review(
            database, user_id=user_id, workspace_id=workspace_id, review_id=review_id
        )
        if (
            row.source_id is None
            or row.revision != 0
            or utc(row.expires_at) <= await database_now(database)
        ):
            raise EmailError("This review was erased or expired while the email was read", 410)
        subject, content = (document.subject or "")[:2000], (document.content or "")[:12000]
        classification = classify(subject=subject, content=content, timezone=timezone, now=now)
        truncated = (
            len(document.content or "") > 12000
            or len(document.subject or "") >= 2000
            or document.metadata.get("content_truncated") is True
        )
        if truncated:
            classification["proposed_deadline"] = None
            classification["ambiguities"] = [
                "This message exceeds the review window. Confirm its timing in the original email.",
                *cast(list[str], classification["ambiguities"]),
            ][:3]
        data = {
            "classification": classification,
            "document_hash": source_document_hash(document),
            "correction": {
                "category": classification["category"],
                "not_important": False,
                "course_id": None,
                "title": None,
                "due_at": classification["proposed_deadline"],
                "estimated_minutes": None,
                "confirmed_course": False,
                "confirmed_deadline": False,
            },
            "content_truncated": truncated,
        }
        row.revision = 1
        row.encrypted_payload = seal(row, data, settings)
        await database.commit()
        return await view(database, row, data, user_id=user_id, workspace_id=workspace_id)


async def correct(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    review_id: UUID,
    payload: CorrectionInput,
) -> dict[str, Any]:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    row, data = await current_review(
        database, settings, user_id=user_id, workspace_id=workspace_id, review_id=review_id
    )
    body = payload.model_dump(mode="json")
    previous = data.get("last_correction")
    if isinstance(previous, dict) and previous.get("request_id") == str(payload.request_id):
        if previous.get("payload_hash") != digest(body):
            raise EmailError("This correction identifier was already used for another change")
        return await view(database, row, data, user_id=user_id, workspace_id=workspace_id)
    if row.revision != payload.revision or row.accepted_task_id is not None:
        raise EmailError("This review changed or already created a task. Review its current state")
    if payload.course_id:
        course: StudentCourse = await schedule.get_owned(
            database, StudentCourse, payload.course_id, user_id, workspace_id
        )
        if course.archived:
            raise EmailError("Choose an active course before accepting this email action")
    data["correction"] = payload.model_dump(mode="json", exclude={"request_id", "revision"})
    if payload.not_important:
        data["correction"]["category"] = "ignore"
    data["last_correction"] = {"request_id": str(payload.request_id), "payload_hash": digest(body)}
    row.revision += 1
    row.encrypted_payload = seal(row, data, settings)
    await database.commit()
    return await view(database, row, data, user_id=user_id, workspace_id=workspace_id)


async def accept_task(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    review_id: UUID,
    payload: AcceptInput,
) -> dict[str, Any]:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    row, data = await current_review(
        database, settings, user_id=user_id, workspace_id=workspace_id, review_id=review_id
    )
    acceptance_hash = digest(payload.model_dump(mode="json"))
    if row.accepted_task_id is not None:
        if row.acceptance_hash != acceptance_hash:
            raise EmailError("This review already created a task. Edit the existing task")
        present = await database.scalar(
            select(StudentTask.id).where(
                StudentTask.id == row.accepted_task_id,
                StudentTask.user_id == user_id,
                StudentTask.workspace_id == workspace_id,
            )
        )
        return {
            "task_id": str(row.accepted_task_id),
            "reused": True,
            "available": present is not None,
            "actions_executed": False,
        }
    if payload.revision != row.revision or payload.source_hash != row.source_hash:
        raise EmailError("The selected review or source changed. Review it again")
    correction = data["correction"]
    if (
        correction["not_important"]
        or correction["category"] == "ignore"
        or not correction["title"]
        or correction["estimated_minutes"] is None
        or not correction["confirmed_course"]
        or not correction["confirmed_deadline"]
    ):
        raise EmailError(
            "Review the title, course choice, deadline choice and effort before accepting", 422
        )
    course_id = UUID(correction["course_id"]) if correction["course_id"] else None
    if course_id:
        course: StudentCourse = await schedule.get_owned(
            database, StudentCourse, course_id, user_id, workspace_id
        )
        if course.archived:
            raise EmailError("Choose an active course before accepting this email action")
    identity = digest({"student_email_source": str(row.source_id)})
    duplicate = await database.scalar(
        select(StudentTask.id).where(
            StudentTask.user_id == user_id,
            StudentTask.workspace_id == workspace_id,
            StudentTask.source_identity == identity,
        )
    )
    link = None
    if payload.task_id is not None:
        task: StudentTask = await schedule.get_owned(
            database, StudentTask, payload.task_id, user_id, workspace_id
        )
        link = await database.scalar(
            select(EmailTaskSource).where(
                EmailTaskSource.task_id == task.id,
                EmailTaskSource.user_id == user_id,
                EmailTaskSource.workspace_id == workspace_id,
            )
        )
        if (
            task.revision != payload.task_revision
            or task.source_identity != identity
            or duplicate != task.id
            or link is None
            or task.status != "active"
        ):
            raise EmailError("Choose the current active task already linked to this exact email")
        if task.course_id:
            previous_course: StudentCourse = await schedule.get_owned(
                database, StudentCourse, task.course_id, user_id, workspace_id
            )
            if previous_course.archived:
                raise EmailError("Restore the task's archived course before changing it")
        if task.actual_minutes + correction["estimated_minutes"] > 10080:
            raise EmailError(
                "Review a smaller remaining effort estimate before refreshing this task", 422
            )
        task.revision += 1
        task.estimated_minutes = task.actual_minutes + correction["estimated_minutes"]
    else:
        if duplicate is not None:
            raise EmailError(
                "This email already has a task. Select its current revision to refresh the source"
            )
        await schedule.count_limit(database, StudentTask, user_id, workspace_id, MAX_TASKS)
        task = StudentTask(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            actual_minutes=0,
            difficulty=3,
            priority=3,
            course_weight=1,
            status="active",
            revision=1,
            source_identity=identity,
            source_item_id=None,
            source_revision=None,
            estimated_minutes=correction["estimated_minutes"],
        )
        database.add(task)
    task.course_id, task.title = course_id, correction["title"]
    task.due_at = CorrectionInput.model_validate(
        {**correction, "request_id": uuid4(), "revision": row.revision}
    ).due_at
    task.remaining_minutes = correction["estimated_minutes"]
    await database.flush()
    _, connector, _ = await verified_source(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        source_id=cast(UUID, row.source_id),
        source_hash=row.source_hash,
    )
    durable_authority_hash = await source_authority_hash(database, connector, in_flight=False)
    if link is None:
        link = EmailTaskSource(
            user_id=user_id, workspace_id=workspace_id, task_id=task.id, revision=1
        )
        database.add(link)
    else:
        link.revision += 1
    link.source_id, link.source_hash = row.source_id, row.source_hash
    link.connector_id = row.connector_id
    link.document_hash = data["document_hash"]
    link.authority_hash = durable_authority_hash
    state = await planning.state(database, user_id, workspace_id)
    state.revision += 1
    await planning.invalidate_future(database, user_id, workspace_id, state)
    row.accepted_task_id, row.acceptance_hash = task.id, acceptance_hash
    await database.commit()
    return {
        "task_id": str(task.id),
        "reused": False,
        "available": True,
        "actions_executed": False,
        "notice": "Task saved after your review. No email or calendar event was sent.",
    }


def erase_payload(row: StudentEmailReview) -> None:
    row.encrypted_payload = None
    row.source_id = None
    row.connector_id = None
    row.source_hash = None
    row.authority_hash = None
    row.revision += 1


async def erase_review(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, review_id: UUID, revision: int
) -> dict[str, object]:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    row = await owned_review(
        database, user_id=user_id, workspace_id=workspace_id, review_id=review_id
    )
    if row.encrypted_payload is None and row.source_id is None and row.connector_id is None:
        return {"erased": True, "revision": row.revision}
    if row.revision != revision:
        raise EmailError("This review changed. Refresh before forgetting it")
    erase_payload(row)
    await database.commit()
    return {
        "erased": True,
        "revision": row.revision,
        "notice": "The review was erased. Any explicitly accepted task remains saved.",
    }


async def email_task_snapshots(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, task_ids: list[UUID]
) -> list[dict[str, object]]:
    """Planner must include this read-only projection in freshness and eligibility."""
    await schedule.owner(database, user_id, workspace_id)
    if len(task_ids) > MAX_TASKS:
        raise EmailError("Too many email task sources to check safely", 409)
    rows = await database.scalars(
        select(EmailTaskSource)
        .where(
            EmailTaskSource.user_id == user_id,
            EmailTaskSource.workspace_id == workspace_id,
            EmailTaskSource.task_id.in_(task_ids),
        )
        .order_by(EmailTaskSource.task_id)
        .limit(MAX_TASKS)
    )
    snapshots: list[dict[str, object]] = []
    for row in rows:
        authorized, current_hash, current_authority = False, None, None
        if row.source_id is not None and row.connector_id is not None:
            source = await database.scalar(
                select(ConnectorResource).where(
                    ConnectorResource.id == row.source_id,
                    ConnectorResource.workspace_id == workspace_id,
                )
            )
            if source is not None:
                try:
                    _, connector, _ = await verified_source(
                        database,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        source_id=source.id,
                        source_hash=source.content_hash,
                    )
                    if connector.id == row.connector_id:
                        authorized, current_hash = True, source.content_hash
                        current_authority = await source_authority_hash(
                            database, connector, in_flight=False
                        )
                except (EmailError, coursework.CourseworkError, schedule.StudentError):
                    pass
        needs_review = (
            not authorized
            or current_hash != row.source_hash
            or current_authority != row.authority_hash
        )
        snapshots.append(
            {
                "task_id": str(row.task_id),
                "source_id": str(row.source_id) if row.source_id else None,
                "source_hash": row.source_hash,
                "current_hash": current_hash,
                "source_authority_hash": row.authority_hash,
                "current_authority_hash": current_authority,
                "authorized": authorized,
                "needs_review": needs_review,
                "notice": "Review this email source again before planning this task"
                if needs_review
                else None,
            }
        )
    return snapshots


async def forget_email_data(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    source_id: UUID | None = None,
    connector_ids: list[UUID] | None = None,
) -> dict[str, object]:
    """Caller holds fresh User lock. Erase derived copies; keep content-free retry fences."""
    conditions = [
        StudentEmailReview.user_id == user_id,
        StudentEmailReview.workspace_id == workspace_id,
    ]
    source_conditions = [
        EmailTaskSource.user_id == user_id,
        EmailTaskSource.workspace_id == workspace_id,
    ]
    if source_id is not None:
        conditions.append(StudentEmailReview.source_id == source_id)
        source_conditions.append(EmailTaskSource.source_id == source_id)
    if connector_ids is not None:
        if len(connector_ids) > 200 or source_id is not None:
            raise ValueError("Choose one bounded source erasure scope")
        owned_connectors = select(ConnectorConnection.id).where(
            ConnectorConnection.user_id == user_id,
            ConnectorConnection.workspace_id == workspace_id,
            ConnectorConnection.id.in_(connector_ids),
        )
        conditions.append(StudentEmailReview.connector_id.in_(owned_connectors))
        source_conditions.append(EmailTaskSource.connector_id.in_(owned_connectors))
    rows = list(
        await database.scalars(select(StudentEmailReview).where(*conditions).limit(MAX_RECEIPTS))
    )
    for row in rows:
        erase_payload(row)
    task_ids = list(
        await database.scalars(
            select(EmailTaskSource.task_id).where(*source_conditions).limit(MAX_TASKS)
        )
    )
    if task_ids:
        await planning.invalidate_snapshots(database, user_id, workspace_id)
        await database.execute(
            delete(StudentStudyBlock).where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.task_id.in_(task_ids),
            )
        )
        await database.execute(
            delete(StudentTask).where(
                StudentTask.user_id == user_id,
                StudentTask.workspace_id == workspace_id,
                StudentTask.id.in_(task_ids),
            )
        )
        await database.execute(delete(EmailTaskSource).where(*source_conditions))
        state = await planning.state(database, user_id, workspace_id)
        state.revision += 1
        await planning.invalidate_future(database, user_id, workspace_id, state)
    return {"erased": True, "reviews_erased": len(rows), "derived_tasks_erased": len(task_ids)}


async def invalidate_email_reviews(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, connector_ids: list[UUID]
) -> None:
    """Caller holds User lock. Disconnect erases short-lived text, retaining saved tasks."""
    if not connector_ids or len(connector_ids) > 200:
        if connector_ids:
            raise ValueError("Choose a bounded connection scope")
        return
    owned_connectors = select(ConnectorConnection.id).where(
        ConnectorConnection.user_id == user_id,
        ConnectorConnection.workspace_id == workspace_id,
        ConnectorConnection.id.in_(connector_ids),
    )
    rows = await database.scalars(
        select(StudentEmailReview)
        .where(
            StudentEmailReview.user_id == user_id,
            StudentEmailReview.workspace_id == workspace_id,
            StudentEmailReview.connector_id.in_(owned_connectors),
        )
        .limit(MAX_RECEIPTS)
    )
    for row in rows:
        erase_payload(row)


async def cleanup_email_reviews(database: AsyncSession, *, limit: int = 200) -> int:
    """Bounded TTL cleanup; root registers on its existing maintenance workflow."""
    if not 1 <= limit <= 200:
        raise ValueError("Choose a bounded cleanup batch")
    now = await database_now(database)
    rows = list(
        await database.scalars(
            select(StudentEmailReview)
            .where(
                StudentEmailReview.expires_at <= now,
                or_(
                    StudentEmailReview.purge_after <= now,
                    StudentEmailReview.encrypted_payload.is_not(None),
                    StudentEmailReview.source_id.is_not(None),
                    StudentEmailReview.connector_id.is_not(None),
                ),
            )
            .order_by(StudentEmailReview.user_id, StudentEmailReview.expires_at)
            .limit(limit)
        )
    )
    for row in rows:
        await database.scalar(select(User.id).where(User.id == row.user_id).with_for_update())
        fresh = await database.get(StudentEmailReview, row.id, populate_existing=True)
        if fresh is None:
            continue
        row = fresh
        if utc(row.purge_after) <= now:
            await database.delete(row)
        elif (
            row.encrypted_payload is not None
            or row.source_id is not None
            or row.connector_id is not None
        ):
            erase_payload(row)
    return len(rows)
