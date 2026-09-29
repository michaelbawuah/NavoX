"""Only identifiers enter durable workflow histories, never feed or conversation text."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NewsSourceWork:
    source_id: str
    workspace_id: str
    user_id: str
    request_id: str


@dataclass(frozen=True)
class NewsSourcePage:
    sources: list[NewsSourceWork]
    after: str | None


@dataclass(frozen=True)
class NewsWorkResult:
    status: str
    stored_count: int = 0
    purged_count: int = 0


@dataclass(frozen=True)
class NewsConversationWork:
    turn_id: str
    workspace_id: str
    user_id: str
