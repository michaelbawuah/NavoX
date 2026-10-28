"""Credential-free waitlist with database-backed admission and content-free errors."""

import hashlib
from time import time
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import get_settings
from navox.db.session import get_database_session
from navox.db.waitlist import WaitlistEntry, WaitlistRateLimit


class WaitlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    source: Literal["landing"] = "landing"


async def admit(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    """Atomically count all attempts, including malformed submissions, across workers."""
    now = int(time())
    window = now // 60
    # Trust only the ASGI peer, never user-provided X-Forwarded-For headers here.
    peer = request.client.host if request.client else "unknown"
    client_hash = hashlib.sha256(peer.encode()).hexdigest()
    insert = sqlite_insert if database.get_bind().dialect.name == "sqlite" else pg_insert
    statement = insert(WaitlistRateLimit).values(client_hash=client_hash, window=window, attempts=1)
    counter = statement.on_conflict_do_update(
        index_elements=["client_hash", "window"],
        set_={"attempts": WaitlistRateLimit.attempts + 1},
    ).returning(WaitlistRateLimit.attempts)
    try:
        attempts = (await database.execute(counter)).scalar_one()
        await database.execute(
            delete(WaitlistRateLimit).where(WaitlistRateLimit.window < window - 1)
        )
        await database.commit()
    except SQLAlchemyError:
        await database.rollback()
        raise HTTPException(503, "Sign-ups temporarily unavailable") from None
    if attempts > 5:
        raise HTTPException(429, "Too many attempts", headers={"Retry-After": str(60 - now % 60)})


def create_waitlist_app() -> FastAPI:
    application = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    origins = ["https://navox.net"]
    if get_settings().app_environment.casefold() not in {"prod", "production"}:
        origins.append("http://localhost:8080")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Retry-After"],
        max_age=600,
    )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's default errors echo the submitted email. Return no submitted content.
        return JSONResponse({"detail": "Enter a valid email and source"}, status_code=422)

    @application.post("/waitlist", status_code=201, dependencies=[Depends(admit)])
    async def join(
        payload: WaitlistRequest,
        database: Annotated[AsyncSession, Depends(get_database_session)],
    ) -> dict[str, str]:
        email = str(payload.email).lower()
        insert = sqlite_insert if database.get_bind().dialect.name == "sqlite" else pg_insert
        statement = (
            insert(WaitlistEntry)
            .values(email=email, source=payload.source, school_domain=email.rsplit("@", 1)[1])
            .on_conflict_do_nothing(index_elements=["email"])
            .returning(WaitlistEntry.email)
        )
        try:
            inserted = (await database.execute(statement)).scalar_one_or_none()
            await database.commit()
        except SQLAlchemyError:
            # Never let SQL parameters reach the generic exception logger.
            await database.rollback()
            raise HTTPException(503, "Sign-ups temporarily unavailable") from None
        if inserted is None:
            raise HTTPException(409, "Already on the list")
        return {"status": "created"}

    return application
