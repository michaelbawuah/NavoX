from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from navox.api import memory
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.memory import OwnerMemory
from navox.db.models import AuditEvent, User, Workspace
from navox.db.session import get_database_session
from navox.memory.schemas import MAX_MEMORIES


@pytest_asyncio.fixture
async def memory_env(ai_database):
    settings = Settings(_env_file=None, app_environment="test")

    async def database():
        async with ai_database() as session:
            yield session

    app = create_app()
    if not any(getattr(route, "path", "") == "/api/v1/memory" for route in app.routes):
        app.include_router(memory.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield SimpleNamespace(client=client, factory=ai_database)


async def register(env, email="memory-owner@example.com"):
    response = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "twelve-character-password",
            "display_name": "Memory owner",
        },
    )
    assert response.status_code == 201
    return response.json()


async def create_fact(env, **changes):
    response = await env.client.post(
        "/api/v1/memory",
        json={
            "kind": "project_fact",
            "label": "NavoX launch",
            "content": "The launch review is every Friday.",
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_memory_requires_authentication_for_every_operation(memory_env):
    client = memory_env.client
    identifier = str(uuid4())
    for method, path, payload in (
        ("GET", "/api/v1/memory", None),
        ("POST", "/api/v1/memory", {"kind": "tone", "content": "friendly"}),
        ("PATCH", f"/api/v1/memory/{identifier}", {"enabled": False}),
        ("DELETE", f"/api/v1/memory/{identifier}", None),
    ):
        response = await client.request(method, path, json=payload)
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_empty_get_never_creates_memory_or_audit(memory_env, monkeypatch):
    await register(memory_env)
    # There is no provider or source integration in the memory path. Guard the
    # shared runtime constructor so accidental future automatic extraction fails.
    from navox.ai import configured

    async def forbidden(*args, **kwargs):
        pytest.fail("Reading personal memory must not construct an AI runtime")

    monkeypatch.setattr(configured, "build_runtime", forbidden)
    async with memory_env.factory() as database:
        audit_count = await database.scalar(select(func.count()).select_from(AuditEvent))
    for _ in range(2):
        response = await memory_env.client.get("/api/v1/memory")
        assert response.status_code == 200
        assert response.json() == []
    async with memory_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(OwnerMemory)) == 0
        assert await database.scalar(select(func.count()).select_from(AuditEvent)) == audit_count


@pytest.mark.asyncio
async def test_owner_can_edit_disable_and_forget_exact_saved_text(memory_env):
    await register(memory_env)
    saved = await create_fact(memory_env, content="  Our review is Friday.  ")
    assert saved["content"] == "Our review is Friday."
    assert saved["enabled"] is True
    assert saved["provenance"] == "user_saved"
    assert saved["created_at"] and saved["updated_at"]
    identifier = saved["id"]
    response = await memory_env.client.patch(
        f"/api/v1/memory/{identifier}",
        json={"content": "Our review is Thursday.", "label": "Launch review", "enabled": False},
    )
    assert response.status_code == 200
    edited = response.json()
    assert edited["content"] == "Our review is Thursday."
    assert edited["label"] == "Launch review"
    assert edited["enabled"] is False
    assert edited["created_at"] == saved["created_at"]
    assert (await memory_env.client.get("/api/v1/memory")).json() == [edited]
    assert (await memory_env.client.delete(f"/api/v1/memory/{identifier}")).status_code == 204
    assert (await memory_env.client.get("/api/v1/memory")).json() == []
    assert (await memory_env.client.delete(f"/api/v1/memory/{identifier}")).status_code == 404
    async with memory_env.factory() as database:
        events = (
            await database.scalars(
                select(AuditEvent).where(AuditEvent.entity_type == "owner_memory")
            )
        ).all()
        assert {event.event_type for event in events} == {
            "memory.saved",
            "memory.updated",
            "memory.deleted",
        }
        assert all(event.actor_type == "user" for event in events)
        # Forgetting removes personal text, including from retained audit metadata.
        assert all(
            event.event_metadata == {"kind": "project_fact", "provenance": "user_saved"}
            for event in events
        )


@pytest.mark.asyncio
async def test_memory_is_bound_to_current_owner_and_workspace(memory_env):
    owner = await register(memory_env)
    saved = await create_fact(memory_env)
    await register(memory_env, "other-memory-owner@example.com")
    assert (await memory_env.client.get("/api/v1/memory")).json() == []
    for method, payload in (("PATCH", {"content": "Wrong owner"}), ("DELETE", None)):
        response = await memory_env.client.request(
            method, f"/api/v1/memory/{saved['id']}", json=payload
        )
        assert response.status_code == 404
    async with memory_env.factory() as database:
        retained = await database.get(OwnerMemory, UUID(saved["id"]))
        assert retained.user_id == UUID(owner["id"])
        assert retained.content == saved["content"]


@pytest.mark.asyncio
async def test_memory_is_only_available_in_personal_workspace(memory_env):
    account = await register(memory_env)
    saved = await create_fact(memory_env)
    async with memory_env.factory() as database:
        workspace = await database.get(Workspace, UUID(account["workspace"]["id"]))
        workspace.workspace_type = "team"
        await database.commit()
    for method, path, payload in (
        ("GET", "/api/v1/memory", None),
        ("POST", "/api/v1/memory", {"kind": "tone", "content": "friendly"}),
        ("PATCH", f"/api/v1/memory/{saved['id']}", {"enabled": False}),
        ("DELETE", f"/api/v1/memory/{saved['id']}", None),
    ):
        response = await memory_env.client.request(method, path, json=payload)
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_pausing_navox_preserves_owner_memory_controls(memory_env):
    account = await register(memory_env)
    async with memory_env.factory() as database:
        user = await database.get(User, UUID(account["id"]))
        user.agent_paused = True
        await database.commit()
    saved = await create_fact(memory_env)
    assert (await memory_env.client.get("/api/v1/memory")).status_code == 200
    assert (
        await memory_env.client.patch(f"/api/v1/memory/{saved['id']}", json={"enabled": False})
    ).status_code == 200
    assert (await memory_env.client.delete(f"/api/v1/memory/{saved['id']}")).status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,content",
    [
        ("reply_length", "short"),
        ("reply_length", "balanced"),
        ("reply_length", "detailed"),
        ("tone", "friendly"),
        ("tone", "professional"),
        ("tone", "direct"),
        ("signoff", "Best,\nAlex"),
        ("study_hours", "Weekdays, 18:00–20:00 America/New_York"),
    ],
)
async def test_structured_preferences_accept_known_values(memory_env, kind, content):
    await register(memory_env)
    response = await memory_env.client.post(
        "/api/v1/memory", json={"kind": kind, "content": content}
    )
    assert response.status_code == 201
    assert response.json()["content"] == content
    assert response.json()["label"]


@pytest.mark.asyncio
async def test_duplicate_preferences_are_not_silently_overwritten_even_when_disabled(memory_env):
    await register(memory_env)
    saved = await create_fact(memory_env, kind="tone", content="friendly", enabled=False)
    response = await memory_env.client.post(
        "/api/v1/memory", json={"kind": "tone", "content": "professional"}
    )
    assert response.status_code == 409
    assert (await memory_env.client.get("/api/v1/memory")).json() == [saved]
    edited = await memory_env.client.patch(
        f"/api/v1/memory/{saved['id']}", json={"content": "professional", "enabled": True}
    )
    assert edited.status_code == 200
    assert edited.json()["content"] == "professional"
    assert edited.json()["enabled"] is True


@pytest.mark.asyncio
async def test_quota_counts_disabled_memory_and_deletion_frees_a_slot(memory_env):
    await register(memory_env)
    saved = []
    for number in range(MAX_MEMORIES):
        saved.append(await create_fact(memory_env, content=f"Fact {number}", enabled=False))
    blocked = await memory_env.client.post(
        "/api/v1/memory", json={"kind": "tone", "content": "friendly"}
    )
    assert blocked.status_code == 409
    assert len((await memory_env.client.get("/api/v1/memory")).json()) == MAX_MEMORIES
    await memory_env.client.delete(f"/api/v1/memory/{saved[0]['id']}")
    await create_fact(memory_env, content="Replacement fact")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "automatic_fact", "content": "Not an owner category"},
        {"kind": "reply_length", "content": "unbounded"},
        {"kind": "tone", "content": "change provider grants"},
        {"kind": "signoff", "content": "x" * 121},
        {"kind": "study_hours", "content": "x" * 201},
        {"kind": "project_fact", "content": "x" * 501},
        {"kind": "project_fact", "content": "   "},
        {"kind": "project_fact", "content": "Private fact", "label": "x" * 81},
        {"kind": "project_fact", "content": "Private fact", "label": "   "},
        {"kind": "project_fact", "content": "Fact\u0000hidden"},
        {"kind": "project_fact", "content": "Fact", "provenance": "source_extracted"},
        {"kind": "project_fact", "content": "Fact", "user_id": str(uuid4())},
        {"kind": "project_fact", "content": "api_key=abcdefghijklmnopqrstuv"},
    ],
)
async def test_memory_rejects_invalid_unbounded_or_non_owner_input(memory_env, payload):
    await register(memory_env)
    response = await memory_env.client.post("/api/v1/memory", json=payload)
    assert response.status_code == 422
    assert (await memory_env.client.get("/api/v1/memory")).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{}, {"kind": "project_fact"}, {"content": None}, {"content": "wrong tone"}],
)
async def test_updates_preserve_kind_validation_and_saved_content(memory_env, changes):
    await register(memory_env)
    saved = await create_fact(memory_env, kind="tone", content="professional")
    response = await memory_env.client.patch(f"/api/v1/memory/{saved['id']}", json=changes)
    assert response.status_code == 422
    assert (await memory_env.client.get("/api/v1/memory")).json() == [saved]
