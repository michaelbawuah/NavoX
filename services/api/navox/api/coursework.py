"""Unregistered, opt-in private course workspaces. No model or provider operations."""

import asyncio
import base64
import json
from collections.abc import Awaitable
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.api.auth import (
    CurrentAccount,
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
)
from navox.api.connector_management import require_origin
from navox.core.settings import Settings
from navox.coursework import service
from navox.coursework.schemas import (
    MAX_UPLOAD_BYTES,
    CommandReceipt,
    DeleteInput,
    EmailLinkInput,
    MaterialDetail,
    MaterialKind,
    MaterialUpload,
    MaterialView,
    OverviewUpdate,
)
from navox.coursework.uploads import UploadError, extract_material, read_upload, upload_slot
from navox.db.coursework import CourseMaterial
from navox.student.service import StudentError

router = APIRouter(prefix="/student/courses/{course_id}/workspace", tags=["coursework"])
PRIVATE = {"Cache-Control": "private, no-store"}
JSON_TIMEOUT_SECONDS = 10


def scope(
    account: CurrentAccount, settings: Settings, response: Response, *, allow_forget: bool = False
) -> tuple[UUID, UUID]:
    response.headers.update(PRIVATE)
    if not allow_forget and not getattr(settings, "student_coursework_enabled", False):
        raise HTTPException(
            503, "Course workspaces are not enabled on this deployment", headers=PRIVATE
        )
    return account.user.id, account.workspace.id


async def bounded_json[T: BaseModel](request: Request, model: type[T], limit: int = 8192) -> T:
    """Private validation never includes Pydantic inputs or JSON parser excerpts."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "Use application/json for course data", headers=PRIVATE)
    try:
        declared = request.headers.get("content-length")
        if declared is not None and not 0 <= int(declared) <= limit:
            raise HTTPException(413, "Course data exceeds the request limit", headers=PRIVATE)
        raw = bytearray()
        async with asyncio.timeout(JSON_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > limit:
                    raise HTTPException(
                        413, "Course data exceeds the request limit", headers=PRIVATE
                    )
                raw.extend(chunk)
        return model.model_validate(json.loads(raw))
    except TimeoutError:
        raise HTTPException(408, "The course request took too long", headers=PRIVATE) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "Course data is invalid", headers=PRIVATE) from None


async def result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except (service.CourseworkError, UploadError) as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except StudentError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, ValidationError):
        raise HTTPException(422, "Course data is invalid", headers=PRIVATE) from None


@router.get("/overview")
async def get_overview(
    course_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, object]:
    user_id, workspace_id = scope(account, settings, response)
    return await result(
        service.overview(
            database, settings, user_id=user_id, workspace_id=workspace_id, course_id=course_id
        )
    )


@router.put("/overview", response_model=CommandReceipt)
async def put_overview(
    course_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> CommandReceipt:
    user_id, workspace_id = scope(account, settings, response)
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, OverviewUpdate, 512 * 1024)
    return await result(
        service.save_overview(
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            payload=payload,
        )
    )


@router.get("/materials")
async def get_materials(
    course_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, list[MaterialView]]:
    user_id, workspace_id = scope(account, settings, response)
    return {
        "materials": await result(
            service.list_materials(
                database, settings, user_id=user_id, workspace_id=workspace_id, course_id=course_id
            )
        )
    }


@router.get("/materials/{material_id}", response_model=MaterialDetail)
async def get_material(
    course_id: UUID,
    material_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> MaterialDetail:
    user_id, workspace_id = scope(account, settings, response)
    await result(
        service.require_course(
            database, user_id=user_id, workspace_id=workspace_id, course_id=course_id
        )
    )
    row = await result(
        service.owned_material(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            material_id=material_id,
        )
    )
    return MaterialDetail.model_validate(await result(_detail(row, settings)))


async def _detail(row: CourseMaterial, settings: Settings) -> MaterialDetail:
    return MaterialDetail.model_validate(service.material_view(row, settings, detail=True))


@router.get("/materials/{material_id}/pages/{page}")
async def get_material_page(
    course_id: UUID,
    material_id: UUID,
    page: Annotated[int, Path(ge=1, le=100)],
    revision: Annotated[int, Query(ge=1, le=2_147_483_647)],
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, object]:
    user_id, workspace_id = scope(account, settings, response)
    return await result(
        service.material_page(
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            material_id=material_id,
            revision=revision,
            page=page,
        )
    )


@router.get("/materials/{material_id}/file")
async def download_material(
    course_id: UUID,
    material_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> Response:
    user_id, workspace_id = scope(account, settings, response)
    await result(
        service.require_course(
            database, user_id=user_id, workspace_id=workspace_id, course_id=course_id
        )
    )
    row = await result(
        service.owned_material(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            material_id=material_id,
        )
    )
    try:
        data = service.reveal(row, settings, "material")
        raw = base64.b64decode(data["original"], validate=True)
    except service.CourseworkError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    suffix = (
        ".pdf"
        if data["media_type"] == "application/pdf"
        else ".md"
        if data["media_type"] == "text/markdown"
        else ".txt"
    )
    return Response(
        raw,
        media_type="application/octet-stream",
        headers={
            **PRIVATE,
            "Content-Disposition": f'attachment; filename="course-material{suffix}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


async def receive_material(
    request: Request,
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    material_id: UUID | None,
    title: str,
    request_id: UUID,
    kind: MaterialKind,
    revision: int,
) -> CommandReceipt:
    await service.require_course(
        database, user_id=user_id, workspace_id=workspace_id, course_id=course_id
    )
    await database.rollback()
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            raise UploadError("Invalid upload size", 400) from None
        if size < 0 or size > MAX_UPLOAD_BYTES:
            raise UploadError("Choose a file no larger than 5 MiB", 413)
    try:
        payload = MaterialUpload(
            title=unquote(title, encoding="utf-8", errors="strict"),
            kind=kind,
            request_id=request_id,
            revision=revision,
        )
    except (UnicodeError, ValueError):
        raise UploadError("Choose a material title within 160 characters", 422) from None
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if media_type not in {"application/pdf", "text/plain", "text/markdown"}:
        raise UploadError("Choose PDF, UTF-8 text, or Markdown", 415)
    async with upload_slot():
        raw = await read_upload(request.stream())
        extracted = await extract_material(raw, media_type)
        return await service.save_material(
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            material_id=material_id,
            payload=payload,
            extracted=extracted,
        )


@router.post("/materials", response_model=CommandReceipt, status_code=201)
@router.put("/materials/{material_id}", response_model=CommandReceipt)
async def upload_material(
    course_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
    material_id: UUID | None = None,
) -> CommandReceipt:
    user_id, workspace_id = scope(account, settings, response)
    require_origin(request, settings.web_origin)
    try:
        title = request.headers.get("X-Material-Title", "")
        if not 1 <= len(title) <= 2000:
            raise ValueError
        # Parse privately so framework validation cannot echo a private title or extra fields.
        upload = MaterialUpload.model_validate(
            {
                "title": unquote(title, encoding="utf-8", errors="strict"),
                "request_id": UUID(request.headers.get("X-Request-ID", "")),
                "kind": request.query_params.get("kind", "material"),
                "revision": int(request.query_params.get("revision", "0")),
            }
        )
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Material upload metadata is invalid", headers=PRIVATE) from None
    return await result(
        receive_material(
            request,
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            material_id=material_id,
            title=title,
            request_id=upload.request_id,
            kind=upload.kind,
            revision=upload.revision,
        )
    )


@router.delete("/materials/{material_id}", response_model=CommandReceipt)
async def forget_material(
    course_id: UUID,
    material_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> CommandReceipt:
    user_id, workspace_id = scope(account, settings, response, allow_forget=True)
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, DeleteInput)
    return await result(
        service.delete_material(
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            material_id=material_id,
            revision=payload.revision,
            request_id=payload.request_id,
        )
    )


@router.get("/search")
async def search(
    course_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, object]:
    user_id, workspace_id = scope(account, settings, response)
    return await result(
        service.search_materials(
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            query=request.query_params.get("q", ""),
        )
    )


@router.get("/assignments")
async def get_assignments(
    course_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
) -> dict[str, object]:
    user_id, workspace_id = scope(account, settings, response)
    return await result(
        service.assignments(
            database, user_id=user_id, workspace_id=workspace_id, course_id=course_id, offset=offset
        )
    )


@router.get("/email-links")
async def emails(
    course_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, object]:
    user_id, workspace_id = scope(account, settings, response)
    return await result(
        service.list_email_links(
            database, user_id=user_id, workspace_id=workspace_id, course_id=course_id
        )
    )


@router.get("/email-candidates")
async def email_candidates(
    course_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, object]:
    user_id, workspace_id = scope(account, settings, response)
    return await result(
        service.email_candidates(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            query=request.query_params.get("q", ""),
        )
    )


@router.post("/email-links", response_model=CommandReceipt, status_code=201)
async def add_email(
    course_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> CommandReceipt:
    user_id, workspace_id = scope(account, settings, response)
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, EmailLinkInput)
    return await result(
        service.link_email(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            payload=payload,
        )
    )


@router.delete("/email-links/{link_id}", response_model=CommandReceipt)
async def remove_email(
    course_id: UUID,
    link_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> CommandReceipt:
    user_id, workspace_id = scope(account, settings, response, allow_forget=True)
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, DeleteInput)
    return await result(
        service.unlink_email(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            link_id=link_id,
            revision=payload.revision,
            request_id=payload.request_id,
        )
    )
