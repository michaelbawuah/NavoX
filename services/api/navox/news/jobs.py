"""Only identifiers enter durable workflow histories, never feed or conversation text."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NewsSourceWork:
    source_id: str
    workspace_id: str
    user_id: str
    request_id: str
    # Set by the reconciliation activity only when a reviewed semantic policy is
    # current. Payloads recorded before that field existed deserialize with the
    # safe default, so an older workflow history never defers exact indexing.
    defer: bool = False


@dataclass(frozen=True)
class NewsSourcePage:
    sources: list[NewsSourceWork]
    after: str | None


@dataclass(frozen=True)
class NewsWorkResult:
    status: str
    stored_count: int = 0
    purged_count: int = 0
    # True while deferred rows still need the separate, non-paid exact recovery.
    deferred: bool = False


@dataclass(frozen=True)
class NewsConversationWork:
    turn_id: str
    workspace_id: str
    user_id: str
