from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from navox.api.actions import router as actions_router
from navox.api.agent import router as agent_router
from navox.api.ai import router as ai_router
from navox.api.ai_operations import router as ai_operations_router
from navox.api.auth import router as auth_router
from navox.api.canvas import router as canvas_router
from navox.api.commitment_actions import router as commitment_actions_router
from navox.api.commitments import router as commitments_router
from navox.api.communication import router as communication_router
from navox.api.connections import router as connections_router
from navox.api.connector_events import router as connector_events_router
from navox.api.connector_management import router as connector_management_router
from navox.api.events import router as events_router
from navox.api.generic_rest import router as generic_rest_router
from navox.api.gmail_recheck import router as gmail_recheck_router
from navox.api.health import router as health_router
from navox.api.imports import router as import_router
from navox.api.intelligence import router as intelligence_router
from navox.api.intelligence_evidence import router as intelligence_evidence_router
from navox.api.intelligence_sync import router as intelligence_sync_router
from navox.api.mcp import router as mcp_router
from navox.api.middleware import RequestHardeningMiddleware
from navox.api.news import router as news_router
from navox.api.news_conversations import router as news_conversations_router
from navox.api.news_stories import router as news_stories_router
from navox.api.proactive import router as proactive_router
from navox.api.subscriptions import router as subscriptions_router
from navox.api.today import router as today_router
from navox.api.workspace import router as workspace_router
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
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["Server-Timing", "X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(RequestHardeningMiddleware)
    app.include_router(canvas_router, prefix="/api/v1")
    app.include_router(import_router, prefix="/api/v1")
    app.include_router(generic_rest_router, prefix="/api/v1")
    app.include_router(mcp_router, prefix="/api/v1")
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(news_router, prefix="/api/v1")
    app.include_router(news_stories_router, prefix="/api/v1")
    app.include_router(news_conversations_router, prefix="/api/v1")
    app.include_router(gmail_recheck_router, prefix="/api/v1")
    app.include_router(intelligence_router, prefix="/api/v1")
    app.include_router(intelligence_evidence_router, prefix="/api/v1")
    app.include_router(intelligence_sync_router, prefix="/api/v1")
    app.include_router(workspace_router, prefix="/api/v1")
    app.include_router(proactive_router, prefix="/api/v1")
    app.include_router(subscriptions_router, prefix="/api/v1")
    app.include_router(actions_router, prefix="/api/v1")
    app.include_router(agent_router, prefix="/api/v1")
    app.include_router(ai_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(connections_router, prefix="/api/v1")
    app.include_router(connector_management_router, prefix="/api/v1")
    app.include_router(connector_events_router, prefix="/api/v1")
    app.include_router(commitments_router, prefix="/api/v1")
    app.include_router(communication_router, prefix="/api/v1")
    app.include_router(ai_operations_router, prefix="/api/v1")
    app.include_router(commitment_actions_router, prefix="/api/v1")
    app.include_router(today_router, prefix="/api/v1")
    app.include_router(events_router, prefix="/api/v1")
    return app


app = create_app()
