"""Identifiers-only workflow payloads for the universal connector runtime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectorSyncWork:
    connection_id: str
    user_id: str
    workspace_id: str
    request_id: str
    trigger: str = "scheduled"


@dataclass(frozen=True)
class ConnectorHealthWork:
    connection_id: str
    user_id: str
    workspace_id: str


@dataclass(frozen=True)
class ConnectorDisconnectWork:
    connection_id: str
    user_id: str
    workspace_id: str
    delete_data: bool = False
