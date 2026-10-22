"""Private course facts; all mutations serialize with the shared fresh User lock."""

import base64
import hashlib
import json
import re
from typing import Any, cast
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.builtin.google import GOOGLE_CONNECTOR_KEY
from navox.connectors.builtin.google_gmail import GMAIL_MANIFEST
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.import_storage import cipher
from navox.core.settings import Settings
from navox.coursework.schemas import (
    MAX_BYTES_COURSE,
    MAX_BYTES_OWNER,
    MAX_MATERIALS_COURSE,
    MAX_MATERIALS_OWNER,
    MAX_PAGES,
    MAX_TEXT_CHARS,
    MAX_UPLOAD_BYTES,
    CommandReceipt,
    EmailLinkInput,
    EmailLinkView,
    MaterialDetail,
    MaterialQuote,
    MaterialUpload,
    MaterialView,
    OverviewData,
    OverviewUpdate,
)
from navox.coursework.uploads import ExtractedMaterial
from navox.db.coursework import (
    CourseEmailLink,
    CourseMaterial,
    CourseworkCommand,
    CourseWorkspaceState,
)
from navox.db.lms_feeds import LmsFeedItem
from navox.db.models import (
    Commitment,
    CommitmentSource,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
)
from navox.db.student import StudentCourse
from navox.db.student_planning import StudentTask
from navox.intelligence.attention import ACTIVE
from navox.lms_feeds.schemas import FeedItem
from navox.lms_feeds.service import item_sources_authorized
from navox.providers.google_sources import GMAIL_READ_SCOPE
from navox.student import service as schedule
from navox.student_planning.schemas import MAX_TASKS
from navox.student_planning.service import reviewed_task_views, task_view

MAX_COMMANDS = 2000


class CourseworkError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


async def require_course(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    mutation: bool = False,
) -> StudentCourse:
    await schedule.owner(database, user_id, workspace_id, lock=mutation)
    course = cast(
        StudentCourse,
        await schedule.get_owned(database, StudentCourse, course_id, user_id, workspace_id),
    )
    if mutation and course.archived:
        raise CourseworkError("Restore this archived course before changing its workspace", 409)
    return course


def seal(
    payload: dict[str, Any],
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    entity_id: UUID,
    revision: int,
    purpose: str,
) -> str:
    envelope = {
        "version": 1,
        "user_id": str(user_id),
        "workspace_id": str(workspace_id),
        "course_id": str(course_id),
        "id": str(entity_id),
        "revision": revision,
        "purpose": purpose,
        "payload": payload,
    }
    try:
        return (
            cipher(settings)
            .encrypt(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode())
            .decode()
        )
    except ConnectorRuntimeError:
        raise CourseworkError("Private course encryption is not configured", 503) from None


def reveal(
    row: CourseMaterial | CourseWorkspaceState, settings: Settings, purpose: str
) -> dict[str, Any]:
    try:
        data = json.loads(cipher(settings).decrypt(row.encrypted_payload.encode()))
        if (
            data["user_id"],
            data["workspace_id"],
            data["course_id"],
            data["id"],
            data["revision"],
            data["purpose"],
            data["version"],
        ) != (
            str(row.user_id),
            str(row.workspace_id),
            str(row.course_id),
            str(row.id),
            row.revision,
            purpose,
            1,
        ):
            raise ValueError
        if not isinstance(data["payload"], dict):
            raise ValueError
        return cast(dict[str, Any], data["payload"])
    except (ConnectorRuntimeError, InvalidToken, UnicodeError, ValueError, KeyError, TypeError):
        raise CourseworkError("This private course item cannot be decrypted", 503) from None


async def owned_material(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, course_id: UUID, material_id: UUID
) -> CourseMaterial:
    row = await database.scalar(
        select(CourseMaterial)
        .where(
            CourseMaterial.id == material_id,
            CourseMaterial.user_id == user_id,
            CourseMaterial.workspace_id == workspace_id,
            CourseMaterial.course_id == course_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise CourseworkError("Course material was not found", 404)
    return row


async def state_row(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, course_id: UUID
) -> CourseWorkspaceState | None:
    return cast(
        CourseWorkspaceState | None,
        await database.scalar(
            select(CourseWorkspaceState)
            .where(
                CourseWorkspaceState.user_id == user_id,
                CourseWorkspaceState.workspace_id == workspace_id,
                CourseWorkspaceState.course_id == course_id,
            )
            .execution_options(populate_existing=True)
        ),
    )


async def command(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    request_id: UUID,
    operation: str,
    payload: object,
) -> CommandReceipt | None:
    row = await database.scalar(
        select(CourseworkCommand).where(
            CourseworkCommand.user_id == user_id,
            CourseworkCommand.workspace_id == workspace_id,
            CourseworkCommand.request_id == request_id,
        )
    )
    if row is not None:
        if (
            row.course_id != course_id
            or row.operation != operation
            or row.payload_hash != digest(payload)
        ):
            raise CourseworkError(
                "This request identifier was already used for another change", 409
            )
        result = CommandReceipt.model_validate(
            {**row.result, "request_id": request_id, "reused": True}
        )
        target = (
            CourseMaterial
            if operation == "material.save"
            else CourseEmailLink
            if operation == "email.link"
            else None
        )
        if target is not None:
            result.available = (
                await database.scalar(
                    select(target.id).where(
                        target.id == result.entity_id,
                        target.user_id == user_id,
                        target.workspace_id == workspace_id,
                        target.course_id == course_id,
                    )
                )
                is not None
            )
        return result
    # Erasure remains available at the creation quota. Keep its content-free
    # receipt: dropping old create receipts would let an old POST restore data.
    if operation in {"material.delete", "email.unlink"}:
        return None
    count = await database.scalar(
        select(func.count())
        .select_from(CourseworkCommand)
        .where(CourseworkCommand.user_id == user_id, CourseworkCommand.workspace_id == workspace_id)
    )
    if (count or 0) >= MAX_COMMANDS:
        raise CourseworkError(
            "The coursework change limit was reached. Review saved course data.", 409
        )
    return None


def save_receipt(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    request_id: UUID,
    operation: str,
    payload: object,
    entity_id: UUID,
    revision: int,
    available: bool = True,
) -> CommandReceipt:
    result = CommandReceipt(
        request_id=request_id, entity_id=entity_id, revision=revision, available=available
    )
    database.add(
        CourseworkCommand(
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            request_id=request_id,
            operation=operation,
            payload_hash=digest(payload),
            result=result.model_dump(mode="json", exclude={"request_id", "reused"}),
        )
    )
    return result


async def check_quote(
    database: AsyncSession,
    quote: MaterialQuote,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    syllabus: bool = False,
) -> None:
    row = await owned_material(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        material_id=quote.material_id,
    )
    if row.revision != quote.revision:
        raise CourseworkError(
            "The source material changed. Select a quote from its current revision.", 409
        )
    if syllabus and row.kind != "syllabus":
        raise CourseworkError("Select an exact quote from a saved syllabus", 422)
    pages = reveal(row, settings, "material")["pages"]
    if (
        not quote.quote.strip()
        or quote.page > len(pages)
        or quote.quote not in pages[quote.page - 1]
    ):
        raise CourseworkError("The exact quote is not on this page of the selected source", 422)


async def overview(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
) -> dict[str, object]:
    course = await require_course(
        database, user_id=user_id, workspace_id=workspace_id, course_id=course_id
    )
    row = await state_row(database, user_id, workspace_id, course_id)
    data = OverviewData.model_validate(reveal(row, settings, "overview")) if row else OverviewData()
    courses = await schedule.course_views(database, user_id, workspace_id, archived=course.archived)
    selected = next(item for item in courses if item.id == course_id)
    return {
        "course": selected.model_dump(mode="json"),
        "archived": course.archived,
        "revision": row.revision if row else 0,
        **data.model_dump(mode="json"),
        "grade_weight_total": str(sum(item.weight_percent for item in data.grade_breakdown)),
        "policy_status": "quoted_policy" if data.ai_policy else "unknown",
        "default_help_mode": "tutor_outline_review",
        "ai_permission_granted": False,
        "notice": (
            "Saved source quotes are displayed verbatim. "
            "No AI policy is inferred and no AI service is enabled."
        ),
    }


async def save_overview(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    payload: OverviewUpdate,
) -> CommandReceipt:
    await require_course(
        database, user_id=user_id, workspace_id=workspace_id, course_id=course_id, mutation=True
    )
    body = payload.model_dump(mode="json")
    previous = await command(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=payload.request_id,
        operation="overview.save",
        payload=body,
    )
    if previous:
        return previous
    row = await state_row(database, user_id, workspace_id, course_id)
    if payload.revision != (row.revision if row else 0):
        raise CourseworkError("The course overview changed. Refresh before saving.", 409)
    sources = [item.source for item in payload.staff] + [
        item.source for item in payload.grade_breakdown
    ]
    for source in sources:
        if source:
            await check_quote(
                database,
                source,
                settings,
                user_id=user_id,
                workspace_id=workspace_id,
                course_id=course_id,
            )
    if payload.ai_policy:
        await check_quote(
            database,
            payload.ai_policy,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            syllabus=True,
        )
    if row is None:
        row = CourseWorkspaceState(
            id=uuid4(),
            course_id=course_id,
            user_id=user_id,
            workspace_id=workspace_id,
            revision=0,
            encrypted_payload="",
        )
        database.add(row)
    row.revision += 1
    row.encrypted_payload = seal(
        payload.model_dump(mode="json", exclude={"request_id", "revision"}),
        settings,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        entity_id=row.id,
        revision=row.revision,
        purpose="overview",
    )
    result = save_receipt(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=payload.request_id,
        operation="overview.save",
        payload=body,
        entity_id=row.id,
        revision=row.revision,
    )
    await database.commit()
    return result


async def clear_derived(
    database: AsyncSession, settings: Settings, material: CourseMaterial
) -> None:
    from navox.student_recovery.service import invalidate_recovery_previews

    await invalidate_recovery_previews(database, material.user_id, material.workspace_id)
    row = await state_row(database, material.user_id, material.workspace_id, material.course_id)
    if row is None:
        return
    data = OverviewData.model_validate(reveal(row, settings, "overview"))
    data.staff = [
        item for item in data.staff if not item.source or item.source.material_id != material.id
    ]
    data.grade_breakdown = [
        item
        for item in data.grade_breakdown
        if not item.source or item.source.material_id != material.id
    ]
    if data.ai_policy and data.ai_policy.material_id == material.id:
        data.ai_policy = None
    row.revision += 1
    row.encrypted_payload = seal(
        data.model_dump(mode="json"),
        settings,
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        course_id=row.course_id,
        entity_id=row.id,
        revision=row.revision,
        purpose="overview",
    )


async def material_quota(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    raw_size: int,
    existing: CourseMaterial | None,
) -> None:
    for course_scope, count_limit, byte_limit in (
        (False, MAX_MATERIALS_OWNER, MAX_BYTES_OWNER),
        (True, MAX_MATERIALS_COURSE, MAX_BYTES_COURSE),
    ):
        conditions = [
            CourseMaterial.user_id == user_id,
            CourseMaterial.workspace_id == workspace_id,
        ]
        if course_scope:
            conditions.append(CourseMaterial.course_id == course_id)
        count, size = (
            await database.execute(
                select(func.count(), func.coalesce(func.sum(CourseMaterial.byte_size), 0))
                .select_from(CourseMaterial)
                .where(*conditions)
            )
        ).one()
        if (
            count + (0 if existing else 1) > count_limit
            or size - (existing.byte_size if existing else 0) + raw_size > byte_limit
        ):
            raise CourseworkError(
                "Saved materials reached the course or account storage limit", 409
            )


async def save_material(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    payload: MaterialUpload,
    extracted: ExtractedMaterial,
    material_id: UUID | None = None,
) -> CommandReceipt:
    await require_course(
        database, user_id=user_id, workspace_id=workspace_id, course_id=course_id, mutation=True
    )
    if (
        not 0 < len(extracted.original) <= MAX_UPLOAD_BYTES
        or not 1 <= len(extracted.pages) <= MAX_PAGES
        or sum(map(len, extracted.pages)) > MAX_TEXT_CHARS
        or not any(page.strip() for page in extracted.pages)
    ):
        raise CourseworkError("Material exceeds its size, page, or readable-text limit", 422)
    content_hash = hashlib.sha256(extracted.original).hexdigest()
    body = {
        **payload.model_dump(mode="json"),
        "material_id": str(material_id) if material_id else None,
        "content_hash": content_hash,
        "media_type": extracted.media_type,
    }
    previous = await command(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=payload.request_id,
        operation="material.save",
        payload=body,
    )
    if previous:
        return previous
    row = (
        await owned_material(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            material_id=material_id,
        )
        if material_id
        else None
    )
    if payload.revision != (row.revision if row else 0):
        raise CourseworkError("The material changed. Refresh before saving.", 409)
    await material_quota(database, user_id, workspace_id, course_id, len(extracted.original), row)
    if row:
        await clear_derived(database, settings, row)
    else:
        row = CourseMaterial(
            id=uuid4(), user_id=user_id, workspace_id=workspace_id, course_id=course_id, revision=0
        )
        database.add(row)
    row.kind, row.revision = payload.kind, row.revision + 1
    row.byte_size, row.text_size, row.page_count = (
        len(extracted.original),
        sum(map(len, extracted.pages)),
        len(extracted.pages),
    )
    row.content_hash = content_hash
    row.encrypted_payload = seal(
        {
            "title": payload.title,
            "media_type": extracted.media_type,
            "original": base64.b64encode(extracted.original).decode(),
            "pages": extracted.pages,
        },
        settings,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        entity_id=row.id,
        revision=row.revision,
        purpose="material",
    )
    result = save_receipt(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=payload.request_id,
        operation="material.save",
        payload=body,
        entity_id=row.id,
        revision=row.revision,
    )
    await database.commit()
    return result


def material_view(
    row: CourseMaterial, settings: Settings, *, detail: bool = False
) -> MaterialView | MaterialDetail:
    data = reveal(row, settings, "material")
    values = {
        "id": row.id,
        "title": data["title"],
        "kind": row.kind,
        "revision": row.revision,
        "media_type": data["media_type"],
        "byte_size": row.byte_size,
        "text_size": row.text_size,
        "page_count": row.page_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    return (
        MaterialDetail.model_validate({**values, "pages": data["pages"]})
        if detail
        else MaterialView.model_validate(values)
    )


async def material_page(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    material_id: UUID,
    revision: int,
    page: int,
) -> dict[str, object]:
    """A bounded literal page read; no model, derived answer or persisted excerpt."""
    await require_course(database, user_id=user_id, workspace_id=workspace_id, course_id=course_id)
    row = await owned_material(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        material_id=material_id,
    )
    if row.revision != revision:
        raise CourseworkError("This course material changed; open its current version", 409)
    data = reveal(row, settings, "material")
    if not 1 <= page <= len(data["pages"]):
        raise CourseworkError("Course material page was not found", 404)
    raw = data["pages"][page - 1].encode("utf-8")
    text = raw[:2048].decode("utf-8", errors="ignore")
    return {
        "course_id": str(course_id),
        "material_id": str(row.id),
        "revision": row.revision,
        "title": data["title"],
        "page": page,
        "text": text,
        "truncated": len(text.encode("utf-8")) < len(raw),
    }


async def list_materials(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
) -> list[MaterialView]:
    await require_course(database, user_id=user_id, workspace_id=workspace_id, course_id=course_id)
    rows = await database.scalars(
        select(CourseMaterial)
        .where(
            CourseMaterial.user_id == user_id,
            CourseMaterial.workspace_id == workspace_id,
            CourseMaterial.course_id == course_id,
        )
        .order_by(CourseMaterial.created_at, CourseMaterial.id)
        .limit(MAX_MATERIALS_COURSE)
    )
    return [material_view(row, settings) for row in rows]


async def delete_material(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    material_id: UUID,
    revision: int,
    request_id: UUID,
) -> CommandReceipt:
    await require_course(
        database, user_id=user_id, workspace_id=workspace_id, course_id=course_id, mutation=True
    )
    body = {"material_id": str(material_id), "revision": revision}
    previous = await command(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=request_id,
        operation="material.delete",
        payload=body,
    )
    if previous:
        return previous
    row = await owned_material(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        material_id=material_id,
    )
    if row.revision != revision:
        raise CourseworkError("The material changed. Refresh before deleting.", 409)
    await clear_derived(database, settings, row)
    await database.delete(row)
    result = save_receipt(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=request_id,
        operation="material.delete",
        payload=body,
        entity_id=material_id,
        revision=revision,
        available=False,
    )
    await database.commit()
    return result


async def search_materials(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    query: str,
) -> dict[str, object]:
    await require_course(database, user_id=user_id, workspace_id=workspace_id, course_id=course_id)
    terms = list(dict.fromkeys(re.findall(r"\w+", query.casefold())))
    if not 1 <= len(query) <= 100 or not terms or len(terms) > 8:
        raise CourseworkError("Search for 1 to 8 words, within 100 characters", 422)
    rows = await database.scalars(
        select(CourseMaterial)
        .where(
            CourseMaterial.user_id == user_id,
            CourseMaterial.workspace_id == workspace_id,
            CourseMaterial.course_id == course_id,
        )
        .order_by(CourseMaterial.created_at.desc(), CourseMaterial.id)
        .limit(MAX_MATERIALS_COURSE)
    )
    results: list[dict[str, object]] = []
    for row in rows:
        data = reveal(row, settings, "material")
        for page, text in enumerate(data["pages"], 1):
            lowered = text.casefold()
            if all(term in lowered for term in terms):
                position = min(lowered.index(term) for term in terms)
                start = max(0, position - 80)
                results.append(
                    {
                        "material_id": str(row.id),
                        "revision": row.revision,
                        "title": data["title"],
                        "page": page,
                        "snippet": text[start : start + 320],
                    }
                )
                if len(results) >= 8:
                    return {"results": results, "limit_reached": True, "course_id": str(course_id)}
    return {"results": results, "limit_reached": False, "course_id": str(course_id)}


async def email_source(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, source_id: UUID, source_hash: str
) -> ConnectorResource:
    resource = await database.scalar(
        select(ConnectorResource)
        .where(
            ConnectorResource.id == source_id,
            ConnectorResource.workspace_id == workspace_id,
            ConnectorResource.resource_type == "communication.message",
            ConnectorResource.deleted.is_(False),
        )
        .execution_options(populate_existing=True)
    )
    if (
        resource is None
        or resource.content_hash != source_hash
        or resource.canonical.get("status") == "deleted"
    ):
        raise CourseworkError("The email source changed or is unavailable", 409)
    try:
        connector = await owned_connector(
            database,
            connection_id=resource.connector_connection_id,
            user_id=user_id,
            workspace_id=workspace_id,
            require_active=True,
            lock_connection=False,
        )
    except ConnectorAccessDenied:
        raise CourseworkError("The email source is no longer authorized", 403) from None
    if (
        resource.provider != connector.provider
        or "communication.messages.read" not in connector.authorized_capabilities
        or "communication.messages.read" not in connector.provider_capabilities
    ):
        raise CourseworkError("The email source is no longer authorized", 403)
    if connector.legacy_connection_id:
        legacy = await database.get(
            Connection, connector.legacy_connection_id, populate_existing=True
        )
        if (
            legacy is None
            or (legacy.user_id, legacy.workspace_id) != (user_id, workspace_id)
            or legacy.status != "active"
            or (legacy.provider == "google" and GMAIL_READ_SCOPE not in legacy.granted_scopes)
        ):
            raise CourseworkError("The email source is no longer authorized", 403)
    return resource


async def link_email(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    payload: EmailLinkInput,
) -> CommandReceipt:
    await require_course(
        database, user_id=user_id, workspace_id=workspace_id, course_id=course_id, mutation=True
    )
    body = payload.model_dump(mode="json")
    previous = await command(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=payload.request_id,
        operation="email.link",
        payload=body,
    )
    if previous:
        return previous
    await email_source(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        source_id=payload.source_id,
        source_hash=payload.source_hash,
    )
    row = await database.scalar(
        select(CourseEmailLink).where(
            CourseEmailLink.user_id == user_id,
            CourseEmailLink.workspace_id == workspace_id,
            CourseEmailLink.course_id == course_id,
            CourseEmailLink.source_id == payload.source_id,
        )
    )
    if row is None:
        count = await database.scalar(
            select(func.count())
            .select_from(CourseEmailLink)
            .where(
                CourseEmailLink.user_id == user_id,
                CourseEmailLink.workspace_id == workspace_id,
                CourseEmailLink.course_id == course_id,
            )
        )
        if (count or 0) >= 100:
            raise CourseworkError("This course already has 100 explicit email links", 409)
        row = CourseEmailLink(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            source_id=payload.source_id,
            source_hash=payload.source_hash,
            revision=1,
        )
        database.add(row)
    elif row.source_hash != payload.source_hash:
        row.source_hash, row.revision = payload.source_hash, row.revision + 1
    result = save_receipt(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=payload.request_id,
        operation="email.link",
        payload=body,
        entity_id=row.id,
        revision=row.revision,
    )
    await database.commit()
    return result


async def list_email_links(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, course_id: UUID
) -> dict[str, object]:
    await require_course(database, user_id=user_id, workspace_id=workspace_id, course_id=course_id)
    rows = await database.scalars(
        select(CourseEmailLink)
        .where(
            CourseEmailLink.user_id == user_id,
            CourseEmailLink.workspace_id == workspace_id,
            CourseEmailLink.course_id == course_id,
        )
        .order_by(CourseEmailLink.id)
        .limit(100)
    )
    links = []
    unavailable = 0
    for row in rows:
        try:
            source = await email_source(
                database,
                user_id=user_id,
                workspace_id=workspace_id,
                source_id=row.source_id,
                source_hash=row.source_hash,
            )
        except CourseworkError:
            unavailable += 1
            continue
        view = EmailLinkView.model_validate(row)
        view.title, view.sender_label = email_labels(source)
        view.display_label = await email_saved_label(
            database, source, user_id=user_id, workspace_id=workspace_id
        )
        if source.provider == "google" and re.fullmatch(r"[a-fA-F0-9]{8,128}", source.external_id):
            view.source_open_url = "https://mail.google.com/mail/u/0/#all/" + source.external_id
        links.append(view.model_dump(mode="json"))
    return {"links": links, "unavailable_count": unavailable}


def email_labels(source: ConnectorResource) -> tuple[str | None, str | None]:
    """Only display explicitly persisted canonical labels, never bodies or metadata."""

    def label(keys: tuple[str, ...], limit: int) -> str | None:
        for key in keys:
            value = source.canonical.get(key)
            if isinstance(value, str):
                value = value[:limit].strip()
                if value and not any(ord(char) < 32 for char in value):
                    return value
        return None

    return label(("subject", "title"), 160), label(("sender_label",), 120)


async def email_saved_label(
    database: AsyncSession, source: ConnectorResource, *, user_id: UUID, workspace_id: UUID
) -> str | None:
    """An existing saved item's title is a label, never a retained email subject."""
    if source.provider != "google" or not re.fullmatch(r"[a-fA-F0-9]{8,128}", source.external_id):
        return None
    title = await database.scalar(
        select(Commitment.title)
        .join(CommitmentSource, CommitmentSource.commitment_id == Commitment.id)
        .join(
            ConnectorConnection,
            ConnectorConnection.legacy_connection_id == CommitmentSource.connection_id,
        )
        .where(
            Commitment.user_id == user_id,
            Commitment.workspace_id == workspace_id,
            Commitment.status.in_(ACTIVE),
            CommitmentSource.provider == "google",
            CommitmentSource.source_type == "gmail_message",
            CommitmentSource.external_resource_id == source.external_id,
            ConnectorConnection.id == source.connector_connection_id,
            ConnectorConnection.user_id == user_id,
            ConnectorConnection.workspace_id == workspace_id,
            ConnectorConnection.provider == "google",
        )
        .order_by(Commitment.updated_at.desc(), Commitment.id)
        .limit(1)
    )
    return "Saved item: " + " ".join(title[:160].split()) if title and title.strip() else None


async def email_candidates(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, course_id: UUID, query: str
) -> dict[str, object]:
    await require_course(database, user_id=user_id, workspace_id=workspace_id, course_id=course_id)
    query = query.strip()
    if not 2 <= len(query) <= 120 or any(ord(char) < 32 for char in query):
        raise CourseworkError("Search using 2 to 120 characters", 422)
    terms = query.casefold().split()
    if len(terms) > 8:
        raise CourseworkError("Search using at most eight words", 422)
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
                ConnectorDefinition.connector_class == "OAUTH_API",
                ConnectorDefinition.trust_level == "NAVOX_FIRST_PARTY",
                ConnectorDefinition.connector_key.in_([GMAIL_MANIFEST.id, GOOGLE_CONNECTOR_KEY]),
                ConnectorConnection.provider == "google",
            )
            .order_by(ConnectorResource.retrieved_at.desc(), ConnectorResource.id)
            .limit(201)
        )
    )
    candidates = []
    for source in rows[:200]:
        try:
            source = await email_source(
                database,
                user_id=user_id,
                workspace_id=workspace_id,
                source_id=source.id,
                source_hash=source.content_hash,
            )
        except CourseworkError:
            continue
        title, sender_label = email_labels(source)
        display_label = await email_saved_label(
            database, source, user_id=user_id, workspace_id=workspace_id
        )
        searchable = " ".join(
            value for value in (title, sender_label, display_label) if value
        ).casefold()
        if not all(term in searchable for term in terms):
            continue
        candidates.append(
            {
                "source_id": str(source.id),
                "source_hash": source.content_hash,
                "title": title,
                "sender_label": sender_label,
                "display_label": display_label,
            }
        )
        if len(candidates) == 20:
            break
    return {
        "candidates": candidates,
        "limit_reached": len(rows) > 200 or len(candidates) == 20,
        "notice": "Search uses recent saved email labels and authorized saved-item titles. "
        "Saved-item labels are not email subjects. Messages without saved labels or saved items "
        "cannot be found here. No mailbox was fetched.",
    }


async def unlink_email(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    link_id: UUID,
    revision: int,
    request_id: UUID,
) -> CommandReceipt:
    await require_course(
        database, user_id=user_id, workspace_id=workspace_id, course_id=course_id, mutation=True
    )
    body = {"link_id": str(link_id), "revision": revision}
    previous = await command(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=request_id,
        operation="email.unlink",
        payload=body,
    )
    if previous:
        return previous
    row = await database.scalar(
        select(CourseEmailLink).where(
            CourseEmailLink.id == link_id,
            CourseEmailLink.user_id == user_id,
            CourseEmailLink.workspace_id == workspace_id,
            CourseEmailLink.course_id == course_id,
        )
    )
    if row is None:
        raise CourseworkError("Email link was not found", 404)
    if row.revision != revision:
        raise CourseworkError("Email link changed. Refresh before removing it.", 409)
    await database.delete(row)
    result = save_receipt(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=course_id,
        request_id=request_id,
        operation="email.unlink",
        payload=body,
        entity_id=link_id,
        revision=revision,
        available=False,
    )
    await database.commit()
    return result


async def assignments(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, course_id: UUID, offset: int = 0
) -> dict[str, object]:
    await require_course(database, user_id=user_id, workspace_id=workspace_id, course_id=course_id)
    rows = await database.scalars(
        select(StudentTask)
        .where(
            StudentTask.user_id == user_id,
            StudentTask.workspace_id == workspace_id,
            StudentTask.course_id == course_id,
        )
        .order_by(StudentTask.created_at, StudentTask.id)
        .limit(MAX_TASKS)
    )
    tasks = []
    imported: set[UUID] = set()
    for row in rows:
        if row.source_item_id:
            item = await database.get(LmsFeedItem, row.source_item_id, populate_existing=True)
            if (
                item is None
                or (item.user_id, item.workspace_id, item.course_id)
                != (user_id, workspace_id, course_id)
                or not await item_sources_authorized(database, item)
            ):
                continue
            imported.add(item.id)
        tasks.append(task_view(row))
    reviewed = await reviewed_task_views(database, user_id, workspace_id, tasks)
    feed_rows = list(
        await database.scalars(
            select(LmsFeedItem)
            .where(
                LmsFeedItem.user_id == user_id,
                LmsFeedItem.workspace_id == workspace_id,
                LmsFeedItem.course_id == course_id,
                LmsFeedItem.id.not_in(imported),
            )
            .order_by(LmsFeedItem.updated_at.desc(), LmsFeedItem.id)
            .offset(offset)
            .limit(51)
        )
    )
    feed_items = []
    for feed_row in feed_rows[:50]:
        if not await item_sources_authorized(database, feed_row):
            continue
        normalized_feed = FeedItem.model_validate(feed_row.payload)
        feed_items.append(
            {
                "id": str(feed_row.id),
                "revision": feed_row.revision,
                "title": normalized_feed.title,
                "kind": normalized_feed.kind,
                "due_at": normalized_feed.due_at.isoformat() if normalized_feed.due_at else None,
                "cancelled": feed_row.cancelled,
                "conflicted": feed_row.conflicted,
                "source": "confirmed_calendar_feed",
            }
        )
    return {
        "tasks": [task.model_dump(mode="json") for task in reviewed],
        "feed_items": feed_items,
        "next_offset": offset + 50 if len(feed_rows) > 50 else None,
    }


async def clear_coursework_data(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, course_id: UUID | None = None
) -> None:
    """Caller holds the shared User lock. No decryption or providers are required."""
    from navox.student_recovery.service import invalidate_recovery_previews

    await invalidate_recovery_previews(database, user_id, workspace_id)
    for model in (CourseEmailLink, CourseMaterial, CourseWorkspaceState, CourseworkCommand):
        conditions = [model.user_id == user_id, model.workspace_id == workspace_id]
        if course_id is not None:
            conditions.append(model.course_id == course_id)
        await database.execute(delete(model).where(*conditions))
