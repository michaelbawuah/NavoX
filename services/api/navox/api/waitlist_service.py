"""Run the public waitlist independently of the student's application release."""

import asyncio

from fastapi import FastAPI
from sqlalchemy.engine import make_url

from navox.api.middleware import RequestHardeningMiddleware
from navox.api.waitlist import create_waitlist_app
from navox.core.settings import get_settings
from navox.db.base import Base
from navox.db.session import get_database_engine


def create_app() -> FastAPI:
    application = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    application.add_middleware(RequestHardeningMiddleware)
    application.mount("/v1", create_waitlist_app())
    return application


async def initialize_database() -> None:
    """Bootstrap only the two public tables, only in the dedicated waitlist DB."""
    if make_url(get_settings().database_url).database != "navox_waitlist":
        raise RuntimeError("Standalone bootstrap requires the dedicated navox_waitlist database")
    engine = get_database_engine()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync,
                    tables=[
                        Base.metadata.tables["waitlist_entries"],
                        Base.metadata.tables["waitlist_rate_limits"],
                    ],
                )
            )
    finally:
        await engine.dispose()


app = create_app()

if __name__ == "__main__":
    asyncio.run(initialize_database())
