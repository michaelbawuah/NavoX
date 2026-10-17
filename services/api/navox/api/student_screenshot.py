"""Explicit, read-only local OCR preview; uploaded bytes are never persisted."""

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response

from navox.api.auth import CurrentAccountDependency
from navox.student.screenshot import (
    MAX_IMAGE_BYTES,
    ScreenshotImportError,
    ScreenshotPreview,
    preview_screenshot,
)

router = APIRouter(prefix="/student/imports/screenshot", tags=["student schedule"])
_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
_UPLOAD_TIMEOUT_SECONDS = 30
_UPLOAD_SLOTS = asyncio.Semaphore(2)


@router.post("/preview", response_model=ScreenshotPreview)
async def preview_screenshot_import(
    request: Request,
    response: Response,
    current_account: CurrentAccountDependency,
) -> ScreenshotPreview:
    if current_account.workspace.workspace_type.casefold() != "personal":
        raise HTTPException(403, "Schedule imports require your personal workspace")
    response.headers["Cache-Control"] = "no-store"
    # Bound images while they are being uploaded, before they reach the OCR slot.
    # Reject rather than retaining a queue of full screenshots in API memory.
    if _UPLOAD_SLOTS.locked():
        raise HTTPException(429, "Screenshot reading is busy. Try again shortly.")
    async with _UPLOAD_SLOTS:
        return await _read_and_preview(request)


async def _read_and_preview(request: Request) -> ScreenshotPreview:
    if (
        request.headers.get("content-type", "").partition(";")[0].strip().casefold()
        not in _MIME_TYPES
    ):
        raise HTTPException(
            415, "Upload a PNG, JPEG or WebP screenshot.", headers={"Cache-Control": "no-store"}
        )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
        except ValueError as error:
            raise HTTPException(400, "Invalid screenshot size") from error
        if length < 0:
            raise HTTPException(400, "Invalid screenshot size")
        if length > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Choose a screenshot smaller than 5 MB.")
    data = bytearray()
    try:
        async with asyncio.timeout(_UPLOAD_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                    raise HTTPException(413, "Choose a screenshot smaller than 5 MB.")
                data.extend(chunk)
    except TimeoutError as error:
        raise HTTPException(408, "The upload took too long. Try a smaller screenshot.") from error
    try:
        return await preview_screenshot(bytes(data))
    except ScreenshotImportError as error:
        raise HTTPException(
            error.status_code, str(error), headers={"Cache-Control": "no-store"}
        ) from error
