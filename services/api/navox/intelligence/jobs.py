"""Identifiers-only workflow payloads; source content never enters workflow history."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceWork:
    connection_id: str
    user_id: str
    workspace_id: str
    source: str
    event_id: str | None = None


@dataclass(frozen=True)
class WorkspaceWork:
    user_id: str
    workspace_id: str
    commitment_id: str | None = None
