"""Source-asserted identity, role, course and thread regressions.

Every fixture here is synthetic canonical data on a disposable database. No
provider, credential, live account or production configuration is read.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select

from navox.db.knowledge import (
    KnowledgeEntity,
    KnowledgeExclusion,
    KnowledgeResource,
    KnowledgeResourcePermission,
)
from navox.db.models import ConnectorConnection, ConnectorDefinition, ConnectorResource
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.knowledge import graph
from navox.knowledge.entities import IDENTITY_ENTITY_TYPE
from navox.knowledge.indexing import backfill_workspace
from navox.knowledge.jobs import KnowledgeResourceWork
from navox.knowledge.lifecycle import process_resource_work
from navox.knowledge.search_contracts import DateRange, ResourceType, SearchRequest
from navox.knowledge.service import search_knowledge
from tests.knowledge_search_support import NOW
from tests.test_knowledge_api import knowledge_api as knowledge_api

GMAIL = "google-gmail"
CALENDAR = "google-calendar"
CANVAS = "canvas-lms"
EMAIL_CAPABILITY = "communication.messages.read"
EVENT_CAPABILITY = "calendar.events.read"
COURSE_CAPABILITY = "academic.courses.read"
ASSIGNMENT_CAPABILITY = "academic.assignments.read"


@dataclass(frozen=True)
class Seeded:
    resource_id: UUID
    source_id: UUID
    entity_id: UUID
    connection_id: UUID


def _manifest(
    connector_key: str, capabilities: list[str], resource_types: list[str]
) -> dict[str, object]:
    return {
        "id": connector_key,
        "version": "1.0.0",
        "displayName": connector_key,
        "category": "test",
        "connectorClass": "GENERIC_API",
        "auth": [{"kind": "none", "label": "None", "scopes": []}],
        "resourceTypes": resource_types,
        "capabilities": {
            "read": [
                {"name": name, "description": name, "sensitive": False} for name in capabilities
            ],
            "write": [],
            "events": [],
            "incrementalSync": True,
        },
        "requiredSecrets": [],
        "rateLimitStrategy": "none",
        "minimumNavoxConnectorApiVersion": "1",
    }


def _unique(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def email_spec(
    external_id: str,
    *,
    subject: str | None = None,
    content: str | None = None,
    author: str | None = None,
    author_name: str | None = None,
    recipients: tuple[str, ...] = (),
    parent: str | None = None,
    connection: str = "gmail",
    identity_type: str = "email",
    created_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "node": "EMAIL",
        "connection": connection,
        "connector_key": GMAIL,
        "provider": "google",
        "provider_type": "communication.message",
        "capability": EMAIL_CAPABILITY,
        "external_id": external_id,
        "subject": subject,
        "content": content,
        "author": author,
        "author_name": author_name,
        "recipients": list(recipients),
        "parent": parent,
        "identity_type": identity_type,
        "created_at": created_at,
    }


def event_spec(
    external_id: str,
    *,
    subject: str | None = None,
    content: str | None = None,
    author: str | None = None,
    author_name: str | None = None,
    recipients: tuple[str, ...] = (),
    start_at: str | None = None,
    parent: str | None = None,
    connection: str = "calendar",
    created_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "node": "EVENT",
        "connection": connection,
        "connector_key": CALENDAR,
        "provider": "google",
        "provider_type": "calendar.event",
        "capability": EVENT_CAPABILITY,
        "external_id": external_id,
        "subject": subject,
        "content": content,
        "author": author,
        "author_name": author_name,
        "recipients": list(recipients),
        "parent": parent,
        "identity_type": "email",
        "start_at": start_at,
        "created_at": created_at,
    }


def course_spec(external_id: str, *, subject: str, connection: str = "canvas") -> dict[str, object]:
    return {
        "node": "COURSE",
        "connection": connection,
        "connector_key": CANVAS,
        "provider": "canvas",
        "provider_type": "academic.course",
        "capability": COURSE_CAPABILITY,
        "external_id": external_id,
        "subject": subject,
        "content": None,
        "author": None,
        "author_name": None,
        "recipients": [],
        "parent": None,
        "identity_type": "email",
    }


def assignment_spec(
    external_id: str,
    *,
    subject: str,
    parent: str,
    content: str | None = None,
    connection: str = "canvas",
) -> dict[str, object]:
    return {
        "node": "COURSE_WORK",
        "connection": connection,
        "connector_key": CANVAS,
        "provider": "canvas",
        "provider_type": "academic.assignment",
        "capability": ASSIGNMENT_CAPABILITY,
        "external_id": external_id,
        "subject": subject,
        "content": content,
        "author": None,
        "author_name": None,
        "recipients": [],
        "parent": parent,
        "identity_type": "email",
    }


def _identity(value: object, name: object, spec: dict[str, object]) -> SourceIdentity | None:
    if not isinstance(value, str) or not value:
        return None
    return SourceIdentity(
        identity_type=str(spec.get("identity_type") or "email"),
        identity_value=value,
        display_name=name if isinstance(name, str) else None,
    )


async def seed_sources(fixture, specs: list[dict[str, object]]) -> dict[str, Seeded]:
    """Create canonical sources, index them, and build their graph anchors."""
    factory = fixture["factory"]
    workspace_id, user_id = fixture["workspace_id"], fixture["user_id"]
    connections: dict[str, ConnectorConnection] = {}
    async with factory() as database:
        definitions: dict[str, ConnectorDefinition] = {}
        for spec in specs:
            connector_key = str(spec["connector_key"])
            if connector_key in definitions:
                continue
            peers = [item for item in specs if item["connector_key"] == connector_key]
            definition = ConnectorDefinition(
                connector_key=connector_key,
                version="1.0.0",
                display_name=f"Fixture {connector_key}",
                connector_class="GENERIC_API",
                trust_level="NAVOX_FIRST_PARTY",
                manifest=_manifest(
                    connector_key,
                    _unique([str(item["capability"]) for item in peers]),
                    _unique([str(item["provider_type"]) for item in peers]),
                ),
            )
            database.add(definition)
            await database.flush()
            definitions[connector_key] = definition
        for spec in specs:
            alias = str(spec.get("connection") or spec["connector_key"])
            connection = connections.get(alias)
            if connection is None:
                connection = ConnectorConnection(
                    connector_definition_id=definitions[str(spec["connector_key"])].id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    provider=str(spec["provider"]),
                    external_account_id=f"account-{alias}",
                    status="CONNECTED",
                    health_state="CONNECTED",
                    authorized_capabilities=_unique(
                        [
                            str(item["capability"])
                            for item in specs
                            if str(item.get("connection") or item["connector_key"]) == alias
                        ]
                    ),
                    provider_capabilities=_unique(
                        [
                            str(item["capability"])
                            for item in specs
                            if str(item.get("connection") or item["connector_key"]) == alias
                        ]
                    ),
                )
                database.add(connection)
                await database.flush()
                connections[alias] = connection
            parent = spec.get("parent")
            metadata: dict[str, object] = {}
            if spec.get("start_at") is not None:
                metadata["start_at"] = spec["start_at"]
            created_at = spec.get("created_at")
            if not isinstance(created_at, datetime):
                created_at = NOW
            resource_id = uuid4()
            document = SourceDocument(
                id=resource_id,
                workspace_id=workspace_id,
                provider=str(spec["provider"]),
                source_type=str(spec["provider_type"]),
                external_id=str(spec["external_id"]),
                external_parent_id=parent if isinstance(parent, str) else None,
                author=_identity(spec.get("author"), spec.get("author_name"), spec),
                recipients=[
                    identity
                    for value in spec.get("recipients") or []
                    if (identity := _identity(value, None, spec)) is not None
                ],
                subject=spec.get("subject") if isinstance(spec.get("subject"), str) else None,
                content=spec.get("content") if isinstance(spec.get("content"), str) else None,
                occurred_at=NOW,
                retrieved_at=NOW,
                metadata=metadata,
            )
            content_hash = sha256(
                f"{spec['external_id']}\x00{spec.get('content') or ''}".encode()
            ).hexdigest()
            database.add(
                ConnectorResource(
                    id=resource_id,
                    workspace_id=workspace_id,
                    connector_connection_id=connection.id,
                    provider=str(spec["provider"]),
                    resource_type=str(spec["provider_type"]),
                    external_id=str(spec["external_id"]),
                    external_parent_id=document.external_parent_id,
                    version="v1",
                    canonical={
                        "source_document": document.model_dump(
                            mode="json", exclude={"retrieved_at"}
                        ),
                        "status": "active",
                    },
                    provider_metadata={},
                    source_url=None,
                    source_created_at=created_at,
                    source_updated_at=created_at,
                    retrieved_at=NOW,
                    content_hash=content_hash,
                    deleted=False,
                )
            )
        await database.commit()
    async with factory() as database:
        report = await backfill_workspace(
            database, workspace_id=workspace_id, user_id=user_id, now=NOW
        )
        assert not report.truncated
        rows = list(
            await database.scalars(
                select(KnowledgeResource).where(KnowledgeResource.workspace_id == workspace_id)
            )
        )
        assert len(rows) == len(specs)
        by_connection = {connection.id: alias for alias, connection in connections.items()}
        seeded: dict[str, Seeded] = {}
        repeated = Counter(str(spec["external_id"]) for spec in specs)
        for row in rows:
            entity_id = await graph.sync_resource_graph(
                database,
                workspace_id=workspace_id,
                user_id=user_id,
                resource_id=row.id,
                now=NOW,
            )
            assert entity_id is not None
            assert row.source_resource_id is not None
            alias = by_connection.get(row.source_connection_id, "")
            key = (
                f"{alias}:{row.external_resource_id}"
                if repeated[row.external_resource_id] > 1
                else row.external_resource_id
            )
            seeded[key] = Seeded(
                resource_id=row.id,
                source_id=row.source_resource_id,
                entity_id=entity_id,
                connection_id=row.source_connection_id,
            )
        await database.commit()
        return seeded


async def related(client, entity_id: UUID) -> dict:
    response = await client.get(f"/api/v1/knowledge/entities/{entity_id}/related")
    assert response.status_code == 200, response.text
    return response.json()


def kinds(body: dict) -> list[str]:
    return [edge["relation_type"] for edge in body["relationships"]]


@pytest.mark.asyncio
async def test_explicit_shared_identity_resolves_roles_and_expands(knowledge_api):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec(
                "message-1",
                subject="Quarterly planning review",
                content="The quarterly planning review covers the budget forecast.",
                author="maya@example.com",
                author_name="Maya",
            ),
            event_spec(
                "event-1",
                subject="Quarterly planning review meeting",
                content="A quarterly planning review with the finance team.",
                author="maya@example.com",
                author_name="Maya Ortiz",
                recipients=("sam@example.com",),
                start_at="2026-10-02T15:00:00+00:00",
            ),
        ],
    )
    message, event = seeded["message-1"], seeded["event-1"]
    body = await related(client, message.entity_id)
    assert kinds(body) == ["SENT", "SAME_SOURCE_IDENTITY"]
    shared = body["relationships"][1]
    assert sorted(shared["evidence_resource_ids"]) == sorted(
        [str(message.resource_id), str(event.resource_id)]
    )
    author_edge = body["relationships"][0]
    assert author_edge["from_entity_id"] != str(message.entity_id)
    assert author_edge["to_entity_id"] == str(message.entity_id)
    assert author_edge["evidence_resource_ids"] == [str(message.resource_id)]
    assert [node["entity_type"] for node in body["entities"]] == [IDENTITY_ENTITY_TYPE, "EVENT"]
    assert body["entities"][1]["resource_id"] == str(event.resource_id)
    assert body["entities"][0]["title"] == "Maya"

    event_body = await related(client, event.entity_id)
    assert kinds(event_body) == ["ORGANIZED", "ATTENDED", "SAME_SOURCE_IDENTITY"]
    titles = sorted(
        node["title"] or ""
        for node in event_body["entities"]
        if node["entity_type"] == IDENTITY_ENTITY_TYPE
    )
    assert titles == ["", "Maya Ortiz"]

    resolution = await client.get(
        f"/api/v1/knowledge/entities/{message.entity_id}/resolution",
        params=[("target_id", str(event.entity_id))],
    )
    assert resolution.status_code == 200, resolution.text
    payload = resolution.json()
    assert payload["state"] == "RESOLVED"
    assert payload["candidates"][0]["basis"] == "EXACT_SOURCE_IDENTITY"
    assert [item["resource_id"] for item in payload["candidates"][0]["evidence"]] == [
        str(message.resource_id),
        str(event.resource_id),
    ]
    assert "maya@example.com" not in resolution.text

    async with knowledge_api["factory"]() as database:
        response = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(query="related quarterly planning review"),
            now=NOW,
        )
    assert {result.resource_id for result in response.results} == {
        message.resource_id,
        event.resource_id,
    }
    assert {item.kind for item in response.relationships} == {"SAME_SOURCE_IDENTITY"}
    assert [sorted(item.evidence_keys) for item in response.relationships] == [
        sorted([f"EMAIL:{message.resource_id}", f"CALENDAR_EVENT:{event.resource_id}"])
    ]


@pytest.mark.asyncio
async def test_same_name_different_identity_never_resolves_or_expands(knowledge_api):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec(
                "alpha",
                subject="Weekly sync",
                content="alphamarker only in the first message",
                author="alpha@example.com",
                author_name="Alex",
            ),
            email_spec(
                "beta",
                subject="Weekly sync",
                content="betamarker only in the second message",
                author="beta@example.com",
                author_name="Alex",
            ),
        ],
    )
    alpha, beta = seeded["alpha"], seeded["beta"]
    body = await related(client, alpha.entity_id)
    assert kinds(body) == ["SENT"]
    assert beta.resource_id not in {UUID(node["resource_id"]) for node in body["entities"]}
    resolution = await client.get(
        f"/api/v1/knowledge/entities/{alpha.entity_id}/resolution",
        params=[("target_id", str(beta.entity_id))],
    )
    assert resolution.json()["state"] == "DISTINCT"
    assert resolution.json()["candidates"][0]["basis"] == "DIFFERENT_SOURCE_IDENTITY"

    async with knowledge_api["factory"]() as database:
        response = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(query="alphamarker"),
            now=NOW,
        )
    assert {result.resource_id for result in response.results} == {alpha.resource_id}
    assert response.relationships == ()


@pytest.mark.asyncio
async def test_name_only_candidates_are_bounded_and_never_resolved(knowledge_api):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec("one", subject="Pat Example", content="first body"),
            email_spec("two", subject="Pat Example", content="second body"),
            email_spec("three", subject="Someone Else", content="third body"),
            email_spec("four", subject="Another Name", content="fourth body"),
            email_spec("five", subject="Yet Another", content="fifth body"),
            email_spec("six", subject="One More", content="sixth body"),
            email_spec("seven", subject="Last One", content="seventh body"),
        ],
    )
    one, two, three = seeded["one"], seeded["two"], seeded["three"]
    assert kinds(await related(client, one.entity_id)) == []
    pair = await client.get(
        f"/api/v1/knowledge/entities/{one.entity_id}/resolution",
        params=[("target_id", str(two.entity_id))],
    )
    assert pair.json()["state"] == "POSSIBLE_MATCH"
    assert pair.json()["candidates"][0]["basis"] == "NAME_ONLY_CANDIDATE"

    many = await client.get(
        f"/api/v1/knowledge/entities/{one.entity_id}/resolution",
        params=[("target_id", str(two.entity_id)), ("target_id", str(three.entity_id))],
    )
    assert many.json()["state"] == "AMBIGUOUS"
    assert [candidate["state"] for candidate in many.json()["candidates"]] == [
        "POSSIBLE_MATCH",
        "AMBIGUOUS",
    ]
    assert many.json()["truncated"] is False
    extra = await client.get(
        f"/api/v1/knowledge/entities/{one.entity_id}/resolution",
        params=[
            ("target_id", str(seeded[external_id].entity_id))
            for external_id in ("two", "three", "four", "five", "six", "seven")
        ],
    )
    assert extra.json()["truncated"] is True
    assert extra.json()["limit"] == graph.RESOLUTION_BOUND
    assert len(extra.json()["candidates"]) == graph.RESOLUTION_BOUND


@pytest.mark.asyncio
async def test_unknown_identity_kind_stays_unavailable(knowledge_api):
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec(
                "named-only",
                subject="Display name only",
                content="body",
                author="Pat Example",
                identity_type="name",
                author_name="Pat Example",
            )
        ],
    )
    async with knowledge_api["factory"]() as database:
        rows = list(
            await database.scalars(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.source_resource_id == seeded["named-only"].resource_id,
                    KnowledgeEntity.entity_type == IDENTITY_ENTITY_TYPE,
                )
            )
        )
    assert rows == []
    body = await related(knowledge_api["client"], seeded["named-only"].entity_id)
    assert body["relationships"] == [] and body["entities"] == []


@pytest.mark.asyncio
async def test_graph_rows_never_persist_identity_text(knowledge_api):
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec(
                "private-1",
                subject="Private note",
                content="body",
                author="hash-me@example.com",
                author_name="Hash Me",
                recipients=("also@example.com",),
            )
        ],
    )
    async with knowledge_api["factory"]() as database:
        rows = list(await database.scalars(select(KnowledgeEntity)))
    assert len(rows) == 3
    for row in rows:
        assert row.display_name == ""
        assert "example.com" not in row.canonical_key
    identity = next(row for row in rows if row.entity_type == IDENTITY_ENTITY_TYPE)
    assert identity.canonical_key.startswith(f"identity:{seeded['private-1'].resource_id}:")
    body = await related(knowledge_api["client"], seeded["private-1"].entity_id)
    assert "example.com" not in str(body)


@pytest.mark.asyncio
async def test_denied_anchor_hides_identity_edges_and_text(knowledge_api):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec("anchor", subject="Shared", content="body", author="owner@example.com"),
            email_spec(
                "sibling",
                subject="Other subject",
                content="other body",
                author="owner@example.com",
                author_name="Owner Name",
            ),
        ],
    )
    anchor, sibling = seeded["anchor"], seeded["sibling"]
    async with knowledge_api["factory"]() as database:
        await database.execute(
            delete(KnowledgeResourcePermission).where(
                KnowledgeResourcePermission.resource_id == sibling.resource_id
            )
        )
        await database.commit()
    statements: list[tuple[str, str]] = []
    engine = knowledge_api["factory"].kw["bind"]

    def capture(_conn, _cursor, statement, parameters, _context, _many):
        statements.append((statement, str(parameters)))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        body = await related(client, anchor.entity_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert sibling.resource_id not in {UUID(node["resource_id"]) for node in body["entities"]}
    assert "SAME_SOURCE_IDENTITY" not in kinds(body)
    assert "Owner Name" not in str(body)
    # Only the stored canonical document carries names and addresses, so that
    # read is the one that must never happen for a denied anchor. The lifecycle
    # flag column is read by the authority check itself and is not identity text.
    identity_reads = [args for sql, args in statements if "connector_resources.canonical" in sql]
    assert not any(sibling.source_id.hex in args.replace("-", "") for args in identity_reads)

    async with knowledge_api["factory"]() as database:
        database.add(
            KnowledgeExclusion(
                workspace_id=knowledge_api["workspace_id"],
                user_id=knowledge_api["user_id"],
                scope="RESOURCE",
                resource_id=anchor.resource_id,
            )
        )
        await database.commit()
    assert (
        await client.get(f"/api/v1/knowledge/entities/{anchor.entity_id}/resolution")
    ).status_code == 422
    resolution = await client.get(
        f"/api/v1/knowledge/entities/{anchor.entity_id}/resolution",
        params=[("target_id", str(sibling.entity_id))],
    )
    assert resolution.status_code == 404


@pytest.mark.asyncio
async def test_disabled_flag_hides_resolution_and_related(knowledge_api):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [email_spec("flag", subject="Flagged", content="body", author="flag@example.com")],
    )
    anchor = seeded["flag"]
    knowledge_api["app"].state.knowledge_settings = knowledge_api["disabled"]
    assert (
        await client.get(f"/api/v1/knowledge/entities/{anchor.entity_id}/related")
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/knowledge/entities/{anchor.entity_id}/resolution",
            params=[("target_id", str(anchor.entity_id))],
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_thread_relation_needs_one_explicit_shared_thread(knowledge_api):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec("thread-a", subject="Same subject", content="a", parent="thread-1"),
            email_spec("thread-b", subject="Same subject", content="b", parent="thread-1"),
            email_spec("thread-c", subject="Same subject", content="c", parent="thread-2"),
            email_spec("thread-none", subject="Same subject", content="d"),
        ],
    )
    first = await related(client, seeded["thread-a"].entity_id)
    assert kinds(first) == ["SAME_THREAD"]
    assert [node["resource_id"] for node in first["entities"]] == [
        str(seeded["thread-b"].resource_id)
    ]
    assert sorted(first["relationships"][0]["evidence_resource_ids"]) == sorted(
        [str(seeded["thread-a"].resource_id), str(seeded["thread-b"].resource_id)]
    )
    # Both directions and repeated reads produce the same bounded relation.
    assert first == await related(client, seeded["thread-a"].entity_id)
    other = await related(client, seeded["thread-b"].entity_id)
    assert kinds(other) == ["SAME_THREAD"]
    assert other["relationships"][0]["id"] == first["relationships"][0]["id"]
    # Same subject in another thread, or no thread at all, is never a relation.
    assert kinds(await related(client, seeded["thread-c"].entity_id)) == []
    assert kinds(await related(client, seeded["thread-none"].entity_id)) == []


@pytest.mark.asyncio
async def test_thread_move_drops_mid_read_relation(knowledge_api, monkeypatch):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec("thread-a", subject="Threaded", content="a", parent="thread-1"),
            email_spec("thread-b", subject="Threaded", content="b", parent="thread-1"),
        ],
    )
    target = seeded["thread-b"]
    original = graph._view

    async def moving(database, workspace_id, user_id, entity, now):
        loaded = await original(database, workspace_id, user_id, entity, now)
        if entity.source_resource_id == target.resource_id and loaded is not None:
            row = await database.get(ConnectorResource, target.source_id)
            row.external_parent_id = "thread-moved"
            await database.flush()
        return loaded

    monkeypatch.setattr(graph, "_view", moving)
    body = await related(client, seeded["thread-a"].entity_id)
    assert kinds(body) == []
    assert body["entities"] == []
    assert body["origin"]["resource_id"] == str(seeded["thread-a"].resource_id)


@pytest.mark.asyncio
async def test_identity_metadata_mutation_drops_mid_read_relation(knowledge_api, monkeypatch):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec("id-a", subject="One", content="a", author="shared@example.com"),
            email_spec(
                "id-b",
                subject="Two",
                content="b",
                author="shared@example.com",
                author_name="Shared Name",
            ),
        ],
    )
    target = seeded["id-b"]
    original = graph._view

    async def rewriting(database, workspace_id, user_id, entity, now):
        loaded = await original(database, workspace_id, user_id, entity, now)
        if entity.source_resource_id == target.resource_id and loaded is not None:
            row = await database.get(ConnectorResource, target.source_id)
            canonical = dict(row.canonical)
            envelope = dict(canonical["source_document"])
            envelope["author"] = {
                "provider": "google",
                "identity_type": "email",
                "identity_value": "someone-else@example.com",
            }
            canonical["source_document"] = envelope
            # The body hash is deliberately unchanged: only identity metadata moved.
            row.canonical = canonical
            await database.flush()
        return loaded

    monkeypatch.setattr(graph, "_view", rewriting)
    body = await related(client, seeded["id-a"].entity_id)
    assert kinds(body) == ["SENT"]
    assert target.resource_id not in {UUID(node["resource_id"]) for node in body["entities"]}
    assert "Shared Name" not in str(body)


@pytest.mark.asyncio
async def test_search_publication_drops_identity_and_thread_moves(knowledge_api, monkeypatch):
    from navox.knowledge import graph_retrieval

    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec(
                "pub-a",
                subject="Related shared review",
                content="related shared review body",
                author="pub@example.com",
            ),
            email_spec(
                "pub-b",
                subject="Sibling note",
                content="sibling body only",
                author="pub@example.com",
            ),
        ],
    )
    original = graph_retrieval.publish_graph

    async def rewriting(database, **kwargs):
        row = await database.get(ConnectorResource, seeded["pub-b"].source_id)
        canonical = dict(row.canonical)
        envelope = dict(canonical["source_document"])
        envelope["author"] = {
            "provider": "google",
            "identity_type": "email",
            "identity_value": "rewritten@example.com",
        }
        canonical["source_document"] = envelope
        row.canonical = canonical
        await database.flush()
        return await original(database, **kwargs)

    monkeypatch.setattr(graph_retrieval, "publish_graph", rewriting)
    async with knowledge_api["factory"]() as database:
        response = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(query="related shared review"),
            now=NOW,
        )
    assert seeded["pub-b"].resource_id not in {result.resource_id for result in response.results}
    assert response.relationships == ()


@pytest.mark.asyncio
async def test_course_membership_is_scoped_to_its_connection(knowledge_api):
    client = knowledge_api["client"]
    seeded = await seed_sources(
        knowledge_api,
        [
            course_spec("course:1", subject="Algorithms"),
            assignment_spec(
                "course:1:assignment:2",
                subject="Problem set 1",
                parent="course:1",
                content="Solve the exercises.",
            ),
            course_spec(
                "course:1", subject="Algorithms (other account)", connection="canvas-other"
            ),
        ],
    )
    assignment = seeded["course:1:assignment:2"]
    course = seeded["canvas:course:1"]
    other_course = seeded["canvas-other:course:1"]
    body = await related(client, assignment.entity_id)
    assert kinds(body) == ["PART_OF", "BELONGS_TO"]
    assert [node["entity_type"] for node in body["entities"]] == ["COURSE"]
    assert [node["resource_id"] for node in body["entities"]] == [str(course.resource_id)]
    assert body["origin"]["entity_type"] == "COURSE_WORK"
    # The identically named course from another connection is never linked.
    assert other_course.resource_id != course.resource_id
    assert str(other_course.resource_id) not in str(body)
    resolution = await client.get(
        f"/api/v1/knowledge/entities/{course.entity_id}/resolution",
        params=[("target_id", str(seeded["course:1:assignment:2"].entity_id))],
    )
    assert resolution.json()["state"] == "AMBIGUOUS"


@pytest.mark.asyncio
async def test_revoked_source_cleanup_and_reindex_have_no_duplicates(knowledge_api):
    factory = knowledge_api["factory"]
    workspace_id, user_id = knowledge_api["workspace_id"], knowledge_api["user_id"]
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec(
                "keep",
                subject="Keep",
                content="keep body",
                author="same@example.com",
                connection="mail-keep",
            ),
            email_spec(
                "gone",
                subject="Gone",
                content="gone body",
                author="same@example.com",
                connection="mail-gone",
            ),
        ],
    )
    keep, gone = seeded["keep"], seeded["gone"]

    async with factory() as database:
        rows = list(await database.scalars(select(KnowledgeEntity)))
        assert len(rows) == 4
        connection = await database.get(ConnectorConnection, gone.connection_id)
        connection.authorized_capabilities = []
        await database.commit()
        payload = KnowledgeResourceWork(str(workspace_id), str(user_id), str(gone.source_id), None)
        result = await process_resource_work(
            database, payload=payload, settings=knowledge_api["disabled"], now=NOW
        )
        assert result == "CLEANED"
        await database.commit()

    async with factory() as database:
        gone_entities = list(
            await database.scalars(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.source_resource_id == gone.resource_id
                )
            )
        )
        assert gone_entities == []
        kept = list(
            await database.scalars(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.source_resource_id == keep.resource_id
                )
            )
        )
        assert len(kept) == 2
        assert (await database.get(KnowledgeResource, keep.resource_id)).deleted_at is None
        connection = await database.get(ConnectorConnection, gone.connection_id)
        connection.authorized_capabilities = [EMAIL_CAPABILITY]
        await database.commit()

    async with factory() as database:
        report = await backfill_workspace(
            database, workspace_id=workspace_id, user_id=user_id, now=NOW
        )
        assert gone.resource_id in {outcome.resource_id for outcome in report.outcomes}
        for resource_id in (keep.resource_id, gone.resource_id):
            await graph.sync_resource_graph(
                database,
                workspace_id=workspace_id,
                user_id=user_id,
                resource_id=resource_id,
                now=NOW,
            )
        await database.commit()
        rows = list(await database.scalars(select(KnowledgeEntity)))
        assert len(rows) == 4
    body = await related(knowledge_api["client"], keep.entity_id)
    assert kinds(body) == ["SENT", "SAME_SOURCE_IDENTITY"]


@pytest.mark.asyncio
async def test_expansion_keeps_global_type_and_date_filters(knowledge_api):
    seeded = await seed_sources(
        knowledge_api,
        [
            email_spec(
                "filtered-mail",
                subject="Quarterly planning review",
                content="quarterly",
                author="filter@example.com",
                created_at=NOW - timedelta(days=2),
            ),
            event_spec(
                "filtered-event",
                subject="Quarterly planning review meeting",
                content="quarterly",
                author="filter@example.com",
                start_at="2026-10-02T15:00:00+00:00",
            ),
        ],
    )
    message, event = seeded["filtered-mail"], seeded["filtered-event"]
    async with knowledge_api["factory"]() as database:
        unfiltered = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(query="related quarterly"),
            now=NOW,
        )
        typed = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(query="related quarterly", types=(ResourceType.CALENDAR_EVENT,)),
            now=NOW,
        )
        windowed = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(
                query="related quarterly",
                date_range=DateRange(start=NOW - timedelta(days=3), end=NOW - timedelta(hours=1)),
            ),
            now=NOW,
        )
    assert {result.resource_id for result in unfiltered.results} == {
        message.resource_id,
        event.resource_id,
    }
    assert {result.resource_id for result in typed.results} == {event.resource_id}
    assert {result.resource_id for result in windowed.results} == {message.resource_id}
