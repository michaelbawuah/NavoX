from fastapi import FastAPI

from navox.api.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="NavoX API",
        version="0.1.0",
        description="Policy-enforced API gateway for the NavoX AI Operations Platform.",
    )
    app.include_router(health_router, prefix="/api/v1")
    return app


app = create_app()
