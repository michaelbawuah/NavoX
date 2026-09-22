from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from navox.api.auth import router as auth_router
from navox.api.commitments import router as commitments_router
from navox.api.connections import router as connections_router
from navox.api.events import router as events_router
from navox.api.health import router as health_router
from navox.core.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="NavoX API",
        version="0.1.0",
        description="Policy-enforced API gateway for the NavoX AI Operations Platform.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(connections_router, prefix="/api/v1")
    app.include_router(commitments_router, prefix="/api/v1")
    app.include_router(events_router, prefix="/api/v1")
    return app


app = create_app()
