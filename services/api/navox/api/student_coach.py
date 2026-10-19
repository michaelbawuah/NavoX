"""Private manual reviews. There is no write, freeform intake or stored receipt."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.student.service import StudentError
from navox.student_coach import service
from navox.student_coach.schemas import CoachReview, Kind

PRIVATE = {"Cache-Control": "private, no-store", "Vary": "Cookie"}


class PrivateCoachRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "The day review request is invalid"},
                    headers=PRIVATE,
                )
            except HTTPException as error:
                return JSONResponse(
                    status_code=error.status_code,
                    content={"detail": error.detail},
                    headers={**(error.headers or {}), **PRIVATE},
                )
            except Exception:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "The day review is unavailable. Try a fresh review later."},
                    headers=PRIVATE,
                )
            response.headers.update(PRIVATE)
            return response

        return private


def enabled(settings: SettingsDependency) -> None:
    if not getattr(settings, "student_coach_enabled", False):
        raise HTTPException(404, "Student day reviews are not enabled", headers=PRIVATE)


router = APIRouter(
    prefix="/student/coach",
    tags=["student coach"],
    route_class=PrivateCoachRoute,
    dependencies=[Depends(enabled)],
)


@router.get("/review", response_model=CoachReview)
async def review(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    kind: Kind = "morning",
) -> CoachReview:
    # The request is deliberately an enum only. No freeform/mood/reason field or body.
    if set(request.query_params) - {"kind"} or len(request.query_params.getlist("kind")) > 1:
        raise HTTPException(422, "The day review request is invalid", headers=PRIVATE)
    if request.headers.get("content-length", "0") != "0" or request.headers.get(
        "transfer-encoding"
    ):
        raise HTTPException(422, "Day reviews do not accept a request body", headers=PRIVATE)
    try:
        async with asyncio.timeout(10):
            return await service.review(
                database, settings, account.user.id, account.workspace.id, kind
            )
    except TimeoutError:
        await database.rollback()
        raise HTTPException(
            503, "The day review took too long. Try a fresh review later.", headers=PRIVATE
        ) from None
    except StudentError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
