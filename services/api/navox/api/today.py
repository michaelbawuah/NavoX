from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from navox.api.auth import CurrentAccountDependency, DatabaseSession
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


class TodayResponse(BaseModel):
    generated_at: str
    timezone: str
    total: int
    needs_attention: list[TodayItemResponse]
    coming_up: list[TodayItemResponse]
    renewals: list[TodayItemResponse]
    waiting_on: list[TodayItemResponse]


QueryIntent = Literal["today", "attention", "this_week", "waiting", "renewals", "promises", "unsupported"]


class TodayQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    timezone: str | None = Field(default=None, max_length=64)


class TodayQueryResponse(BaseModel):
    intent: QueryIntent
    answer: str
    items: list[TodayItemResponse]
    supported_queries: list[str]


SUPPORTED_QUERIES = [
    "What do I need to know today?",
    "What needs my attention?",
    "What is coming up this week?",
    "What am I waiting on?",
    "What renewals are coming up?",
    "What promises have I made?",
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
        sources=[
            TodaySourceResponse(
                provider=source.provider,
                source_type=source.source_type,
                external_resource_id=source.external_resource_id,
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
    timezone_name: str | None = Query(default=None, alias="timezone"),
) -> TodayResponse:
    projection = await current_projection(current_account, database, timezone_name)
    return projection_response(projection)


def classify_query(query: str) -> QueryIntent:
    normalized = " ".join(query.casefold().split())
    if any(word in normalized for word in ("renewal", "renew", "subscription")):
        return "renewals"
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
            "today": "Nothing needs your attention right now, and there is no other active work saved.",
            "attention": "Nothing currently needs your attention.",
            "this_week": "No saved commitments are due in the next 7 days.",
            "waiting": "You are not currently waiting on any saved commitments.",
            "renewals": "No active renewals are currently saved.",
            "promises": "No active promises are currently saved.",
            "unsupported": "",
        }
        return empty_messages[intent]

    if intent == "today":
        return (
            f"You have {projection.total} active commitments. "
            f"{len(projection.needs_attention)} need attention now; "
            f"{len(projection.coming_up)} are coming up."
        )
    labels: dict[QueryIntent, str] = {
        "attention": "need your attention",
        "this_week": "are due within the next 7 days",
        "waiting": "are waiting",
        "renewals": "are active renewals",
        "promises": "are active promises",
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
            and local_now <= item.due_at.astimezone(local_now.tzinfo)
            <= local_now + timedelta(days=7)
        ]
    else:
        selected = []

    return TodayQueryResponse(
        intent=intent,
        answer=format_answer(intent, selected, projection),
        items=[item_response(item) for item in selected[:12]],
        supported_queries=SUPPORTED_QUERIES,
    )
