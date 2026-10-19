from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from navox.api.actions import router as actions_router
from navox.api.agent import router as agent_router
from navox.api.ai import router as ai_router
from navox.api.ai_operations import router as ai_operations_router
from navox.api.auth import router as auth_router
from navox.api.board import router as board_router
from navox.api.canvas import router as canvas_router
from navox.api.commitment_actions import router as commitment_actions_router
from navox.api.commitments import router as commitments_router
from navox.api.communication import router as communication_router
from navox.api.connections import router as connections_router
from navox.api.connector_events import router as connector_events_router
from navox.api.connector_management import router as connector_management_router
from navox.api.coursework import router as coursework_router
from navox.api.events import router as events_router
from navox.api.generic_rest import router as generic_rest_router
from navox.api.gmail_recheck import router as gmail_recheck_router
from navox.api.health import router as health_router
from navox.api.imports import router as import_router
from navox.api.intelligence import router as intelligence_router
from navox.api.intelligence_evidence import router as intelligence_evidence_router
from navox.api.intelligence_sync import router as intelligence_sync_router
from navox.api.knowledge import router as knowledge_router
from navox.api.knowledge_graph import router as knowledge_graph_router
from navox.api.knowledge_lifecycle import router as knowledge_lifecycle_router
from navox.api.lms_feed import router as lms_feed_router
from navox.api.mcp import router as mcp_router
from navox.api.memory import router as memory_router
from navox.api.middleware import RequestHardeningMiddleware
from navox.api.news import router as news_router
from navox.api.news_conversations import router as news_conversations_router
from navox.api.news_following import router as news_following_router
from navox.api.news_stories import router as news_stories_router
from navox.api.proactive import router as proactive_router
from navox.api.routines import router as routines_router
from navox.api.student import router as student_router
from navox.api.student_agent import router as student_agent_router
from navox.api.student_alerts import router as student_alerts_router
from navox.api.student_coach import router as student_coach_router
from navox.api.student_email import router as student_email_router
from navox.api.student_goals import router as student_goals_router
from navox.api.student_planning import router as student_planning_router
from navox.api.student_recovery import router as student_recovery_router
from navox.api.student_screenshot import router as student_screenshot_router
from navox.api.student_work_export import router as student_work_export_router
from navox.api.subscriptions import router as subscriptions_router
from navox.api.thread_watches import router as thread_watches_router
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
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-Material-Title",
            "X-NavoX-Account",
        ],
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
    app.include_router(news_following_router, prefix="/api/v1")
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
    app.include_router(thread_watches_router, prefix="/api/v1")
    app.include_router(routines_router, prefix="/api/v1")
    app.include_router(memory_router, prefix="/api/v1")
    app.include_router(student_router, prefix="/api/v1")
    app.include_router(student_alerts_router, prefix="/api/v1")
    app.include_router(student_screenshot_router, prefix="/api/v1")
    app.include_router(student_planning_router, prefix="/api/v1")
    app.include_router(student_recovery_router, prefix="/api/v1")
    app.include_router(student_email_router, prefix="/api/v1")
    app.include_router(student_goals_router, prefix="/api/v1")
    app.include_router(board_router, prefix="/api/v1")
    app.include_router(student_coach_router, prefix="/api/v1")
    app.include_router(student_work_export_router, prefix="/api/v1")
    app.include_router(lms_feed_router, prefix="/api/v1")
    app.include_router(student_agent_router, prefix="/api/v1")
    app.include_router(coursework_router, prefix="/api/v1")
    app.include_router(ai_operations_router, prefix="/api/v1")
    app.include_router(commitment_actions_router, prefix="/api/v1")
    app.include_router(today_router, prefix="/api/v1")
    app.include_router(events_router, prefix="/api/v1")
    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(knowledge_graph_router, prefix="/api/v1")
    app.include_router(knowledge_lifecycle_router, prefix="/api/v1")
    return app


app = create_app()
