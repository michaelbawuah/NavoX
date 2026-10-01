"""Identifier-only durable connected-knowledge work; never content or secrets."""

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeResourceWork:
    workspace_id: str
    user_id: str
    source_resource_id: str
    expected_content_hash: str | None = None


@dataclass(frozen=True)
class KnowledgeEmbeddingWork:
    workspace_id: str
    user_id: str
    resource_id: str
    request_id: str
    chunk_index: int = 0


@dataclass(frozen=True)
class KnowledgePage:
    resources: list[KnowledgeResourceWork]
    after: str | None
