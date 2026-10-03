from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from navox.api.auth import CurrentAccountDependency, DatabaseSession
from navox.proactive.engine import evaluate_workspace, next_meeting_prep, visible_signals
from navox.today.projection import (
    InvalidTimezoneError,
    TodayItem,
    TodayProjection,
    build_today_projection,
)

router = APIRouter(prefix="/today", tags=["today"])


class TodaySourceResponse(BaseModel):
    provider: str
    source_type: str
    external_resource_id: str | None
    evidence_locator: dict[str, object] | None = None
    observed_at: str | None = None
    evidence_id: UUID | None = None
    connection_id: UUID | None = None


class TodayItemResponse(BaseModel):
    id: UUID
    type: str
    title: str
    description: str | None
    status: str
    priority: int
    due_at: str | None
    confidence: float
    created_by: str
    score: int
    reasons: list[str]
    sources: list[TodaySourceResponse]
    category: str
    band: str
    factors: dict[str, float]
    suggested_capability: str | None


class TodayResponse(BaseModel):
    generated_at: str
    timezone: str
    total: int
    needs_attention: list[TodayItemResponse]
    coming_up: list[TodayItemResponse]
    renewals: list[TodayItemResponse]
    waiting_on: list[TodayItemResponse]
    completed_recently: list[TodayItemResponse]
    set_aside: list[TodayItemResponse]


QueryIntent = Literal[
    "today",
    "attention",
    "this_week",
    "waiting",
    "renewals",
    "promises",
    "forgetting",
    "meeting_prep",
    "handleable",
    "unsupported",
]


class TodayQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    timezone: str | None = Field(default=None, max_length=64)


class TodayQueryResponse(BaseModel):
    intent: QueryIntent
    answer: str
    items: list[TodayItemResponse]
    supported_queries: list[str]
    details: list[str] = Field(default_factory=list)


SUPPORTED_QUERIES = [
    "What do I need to know today?",
    "What am I missing today?",
    "What needs my attention?",
    "What is coming up this week?",
    "What am I waiting on?",
    "What renewals are coming up?",
    "What promises have I made?",
    "What am I forgetting?",
    "Prepare me for my next meeting.",
    "Anything costing me money soon?",
    "What can you handle for me?",
]


def item_response(item: TodayItem) -> TodayItemResponse:
    return TodayItemResponse(
        id=item.id,
        type=item.type,
        title=item.title,
        description=item.description,
        status=item.status,
        priority=item.priority,
        due_at=item.due_at.isoformat() if item.due_at is not None else None,
        confidence=item.confidence,
        created_by=item.created_by,
        score=item.score,
        reasons=list(item.reasons),
        category=item.category,
        band=item.band,
        factors=item.factors,
        suggested_capability=item.suggested_capability,
        sources=[
            TodaySourceResponse(
                provider=source.provider,
                source_type=source.source_type,
                external_resource_id=source.external_resource_id,
                evidence_locator=source.evidence_locator,
                observed_at=source.observed_at.isoformat() if source.observed_at else None,
                evidence_id=source.evidence_id,
                connection_id=source.connection_id,
            )
            for source in item.sources
        ],
    )


def projection_response(projection: TodayProjection) -> TodayResponse:
    return TodayResponse(
        generated_at=projection.generated_at.isoformat(),
        timezone=projection.timezone,
        total=projection.total,
        needs_attention=[item_response(item) for item in projection.needs_attention],
        coming_up=[item_response(item) for item in projection.coming_up],
        renewals=[item_response(item) for item in projection.renewals],
        waiting_on=[item_response(item) for item in projection.waiting_on],
        completed_recently=[item_response(item) for item in projection.completed_recently],
        set_aside=[item_response(item) for item in projection.set_aside],
    )


async def current_projection(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    timezone_name: str | None,
) -> TodayProjection:
    selected_timezone = timezone_name or current_account.user.timezone
    try:
        return await build_today_projection(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            timezone_name=selected_timezone,
        )
    except InvalidTimezoneError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get("", response_model=TodayResponse)
async def get_today(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
    timezone_name: str | None = Query(default=None, alias="timezone"),
) -> TodayResponse:
    response.headers["Cache-Control"] = "no-store"
    projection = await current_projection(current_account, database, timezone_name)
    return projection_response(projection)


def classify_query(query: str) -> QueryIntent:
    normalized = " ".join(query.casefold().split())
    if any(
        word in normalized
        for word in ("renewal", "renew", "subscription", "costing me money", "money soon")
    ):
        return "renewals"
    if any(phrase in normalized for phrase in ("next meeting", "prepare me", "meeting prep")):
        return "meeting_prep"
    if any(phrase in normalized for phrase in ("forgetting", "forgot", "missed something")):
        return "forgetting"
    if any(phrase in normalized for phrase in ("handle for me", "can you handle", "you handle")):
        return "handleable"
    if any(word in normalized for word in ("waiting", "wait on", "waiting on")):
        return "waiting"
    if "promise" in normalized:
        return "promises"
    if any(phrase in normalized for phrase in ("this week", "next 7 days", "coming up")):
        return "this_week"
    if any(word in normalized for word in ("attention", "urgent", "overdue")):
        return "attention"
    if any(word in normalized for word in ("today", "right now", "need to know")):
        return "today"
    return "unsupported"


def deduplicate(items: list[TodayItem]) -> list[TodayItem]:
    seen: set[UUID] = set()
    result: list[TodayItem] = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            result.append(item)
    return result


def format_answer(intent: QueryIntent, items: list[TodayItem], projection: TodayProjection) -> str:
    if intent == "unsupported":
        return (
            "I can answer read-only questions about today, attention, this week, "
            "waiting, renewals, and promises."
        )
    if not items:
        empty_messages: dict[QueryIntent, str] = {
            "today": "Nothing needs your attention right now, "
            "and there is no other active work saved.",
            "attention": "Nothing currently needs your attention.",
            "this_week": "No saved commitments are due in the next 7 days.",
            "waiting": "You are not currently waiting on any saved commitments.",
            "renewals": "No active renewals are currently saved.",
            "promises": "No active promises are currently saved.",
            "forgetting": "NavoX is not currently surfacing anything you appear to be forgetting.",
            "meeting_prep": "No upcoming saved meeting is available to prepare.",
            "handleable": "No active confirmed commitment is currently ready for Handle this.",
            "unsupported": "",
        }
        return empty_messages[intent]

    if intent == "meeting_prep":
        return f"Your next saved meeting is {items[0].title}."
    if intent == "forgetting":
        return f"{len(items)} proactive item(s) are important enough for a second look."
    if intent == "handleable":
        return f"{len(items)} active commitment(s) can enter the bounded Handle this flow."
    if intent == "today":
        attention = len(projection.needs_attention)
        coming = len(projection.coming_up)
        if attention == 0 and coming == 0:
            return "Your saved operational state is clear right now."
        if attention == 0:
            return f"Nothing is urgent right now. {coming} item(s) are coming up."
        return f"{attention} item(s) need attention now; {coming} more are coming up."
    labels: dict[QueryIntent, str] = {
        "attention": "need your attention",
        "this_week": "are due within the next 7 days",
        "waiting": "are waiting",
        "renewals": "are active renewals",
        "promises": "are active promises",
        "forgetting": "are worth a second look",
        "meeting_prep": "are relevant to your next meeting",
        "handleable": "can be handed to the bounded agent",
        "today": "",
        "unsupported": "",
    }
    return f"{len(items)} saved commitments {labels[intent]}."


@router.post("/query", response_model=TodayQueryResponse)
async def query_today(
    payload: TodayQueryRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> TodayQueryResponse:
    projection = await current_projection(current_account, database, payload.timezone)
    intent = classify_query(payload.query)
    primary = list(projection.needs_attention) + list(projection.coming_up)
    all_items = deduplicate(primary)

    details: list[str] = []
    if intent == "today":
        selected = all_items
    elif intent == "attention":
        selected = list(projection.needs_attention)
    elif intent == "waiting":
        selected = list(projection.waiting_on)
    elif intent == "renewals":
        selected = list(projection.renewals)
    elif intent == "promises":
        selected = [item for item in all_items if item.type == "promise"]
    elif intent == "this_week":
        local_now = projection.generated_at
        selected = [
            item
            for item in all_items
            if item.due_at is not None
            and local_now
            <= item.due_at.astimezone(local_now.tzinfo)
            <= local_now + timedelta(days=7)
        ]
    elif intent == "forgetting":
        selected_timezone = payload.timezone or current_account.user.timezone
        await evaluate_workspace(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            timezone_name=selected_timezone,
        )
        signals = await visible_signals(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
        )
        important_ids = {
            signal.commitment_id
            for signal in signals
            if signal.commitment_id is not None and signal.tier in {"notify_now", "briefing"}
        }
        selected = [item for item in all_items if item.id in important_ids]
    elif intent == "meeting_prep":
        prep = await next_meeting_prep(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
        )
        if prep is None:
            selected = []
        else:
            selected = [item for item in all_items if item.id == prep.commitment_id]
            details = list(prep.prep_points)
    elif intent == "handleable":
        selected = [
            item for item in all_items if item.status in {"confirmed", "attention", "waiting"}
        ]
    else:
        selected = []

    return TodayQueryResponse(
        intent=intent,
        answer=format_answer(intent, selected, projection),
        items=[item_response(item) for item in selected[:12]],
        supported_queries=SUPPORTED_QUERIES,
        details=details,
    )
