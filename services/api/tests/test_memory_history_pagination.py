"""All retained history remains reachable through bounded, owner-fenced pages."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_conversation_memory import enable
from test_conversation_memory import history_env as history_env

from navox.db.conversation_memory import MemoryConversation, MemoryPrivacy
from navox.memory import history

ROOT = "/api/v1/memory/history/page"


async def seed(env, count, *, query_at=None, same_time=False):
    generation = await enable(env, days=None)
    now = datetime.now(UTC)
    rows = []
    async with env.factory() as database:
        for index in range(count):
            identifier = UUID(int=index + 1)
            moment = now if same_time else now - timedelta(minutes=index)
            content = "Older synthetic match" if index == query_at else f"Synthetic call {index}"
            transcript = {
                "title": f"Conversation {index}",
                "turns": [
                    {"role": "user", "content": content, "created_at": moment.isoformat()},
                    {"role": "assistant", "content": "Noted.", "created_at": moment.isoformat()},
                ],
            }
            row = MemoryConversation(
                id=identifier,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                source_kind="vox",
                source_id=uuid4(),
                generation=generation,
                last_sequence=1,
                encrypted_transcript=history.encrypt(
                    env.settings,
                    env.user_id,
                    env.workspace_id,
                    identifier,
                    "transcript",
                    transcript,
                ),
                encrypted_summary=history.encrypt(
                    env.settings, env.user_id, env.workspace_id, identifier, "summary", content
                ),
                message_count=2,
                created_at=moment,
                updated_at=moment,
                expires_at=None,
                erased_at=None,
            )
            database.add(row)
            rows.append(row)
        await database.commit()
    return rows


async def page(env, **params):
    response = await env.client.post(ROOT, json=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.request.url.query == b""
    return response.json()


@pytest.mark.asyncio
async def test_pages_reach_more_than_100_conversations_and_older_source_can_be_forgotten(
    history_env,
):
    env = history_env
    rows = await seed(env, 111)
    cursor = None
    identifiers = []
    while True:
        result = await page(env, page_size=25, **({"cursor": cursor} if cursor else {}))
        assert result["scanned"] <= 25
        identifiers.extend(row["id"] for row in result["items"])
        assert result["search_complete"] is (result["next_cursor"] is None)
        cursor = result["next_cursor"]
        if cursor is None:
            break
    assert identifiers == [str(row.id) for row in rows]
    assert len(set(identifiers)) == 111
    # The old list contract is preserved for callers that have not adopted paging.
    assert len((await env.client.get("/api/v1/memory/history")).json()) == 100
    older = identifiers[-1]
    assert (await env.client.get(f"/api/v1/memory/history/{older}")).status_code == 200
    assert (await env.client.delete(f"/api/v1/memory/history/{older}")).status_code == 204
    assert (await env.client.get(f"/api/v1/memory/history/{older}")).status_code == 404


@pytest.mark.asyncio
async def test_search_can_continue_empty_pages_to_find_an_older_than_100_match(history_env):
    env = history_env
    rows = await seed(env, 103, query_at=102)
    cursor = None
    matches = []
    empty_pages = 0
    while True:
        result = await page(
            env,
            query="older synthetic match",
            page_size=25,
            **({"cursor": cursor} if cursor else {}),
        )
        matches.extend(result["items"])
        if not result["items"]:
            empty_pages += 1
        cursor = result["next_cursor"]
        if cursor is None:
            assert result["search_complete"] is True
            break
        assert result["search_complete"] is False
    assert empty_pages == 4
    assert [row["id"] for row in matches] == [str(rows[-1].id)]


@pytest.mark.asyncio
async def test_page_decrypts_only_its_scan_budget_and_does_not_expose_query_in_cursor(
    history_env, monkeypatch
):
    env = history_env
    await seed(env, 5)
    original = history.decrypt
    opened = []

    def decrypt(settings, user_id, workspace_id, record_id, kind, encrypted):
        opened.append((record_id, kind))
        return original(settings, user_id, workspace_id, record_id, kind, encrypted)

    monkeypatch.setattr(history, "decrypt", decrypt)
    result = await page(env, query="private-query-sentinel", page_size=2)
    assert result["items"] == [] and result["scanned"] == 2
    assert result["next_cursor"] and result["search_complete"] is False
    assert "private-query-sentinel" not in result["next_cursor"]
    assert [kind for _, kind in opened].count("transcript") == 2
    assert [kind for _, kind in opened].count("summary") == 2
    assert len({identifier for identifier, _ in opened}) == 2


@pytest.mark.asyncio
async def test_immutable_creation_order_survives_new_turns_and_equal_timestamp_ties(history_env):
    env = history_env
    rows = await seed(env, 5, same_time=True)
    first = await page(env, page_size=2)
    assert [row["id"] for row in first["items"]] == [str(row.id) for row in rows[:2]]
    async with env.factory() as database:
        row = await database.get(MemoryConversation, rows[-1].id)
        row.updated_at = datetime.now(UTC) + timedelta(minutes=5)
        await database.commit()
    second = await page(env, page_size=2, cursor=first["next_cursor"])
    third = await page(env, page_size=2, cursor=second["next_cursor"])
    assert [row["id"] for row in second["items"] + third["items"]] == [
        str(row.id) for row in rows[2:]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["query", "page_size", "tampered", "purpose"])
async def test_cursor_cannot_change_query_budget_position_or_purpose(history_env, changed):
    env = history_env
    await seed(env, 3)
    first = await page(env, query="synthetic", page_size=1)
    token = first["next_cursor"]
    params = {"query": "synthetic", "page_size": 1, "cursor": token}
    if changed == "query":
        params["query"] = "different"
    elif changed == "page_size":
        params["page_size"] = 2
    elif changed == "tampered":
        index = len(token) // 2
        params["cursor"] = (
            token[:index] + ("A" if token[index] != "A" else "B") + token[index + 1 :]
        )
    else:
        params["cursor"] = history.encrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            history.HISTORY_CURSOR_ID,
            "transcript",
            {"private-query-sentinel": True},
        )
    response = await env.client.post(ROOT, json=params)
    assert response.status_code == 409
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {"detail": "This history page changed. Start the search again."}
    assert "private-query-sentinel" not in response.text and token not in response.text


@pytest.mark.asyncio
async def test_cursor_is_bound_to_authenticated_owner_and_workspace(history_env):
    env = history_env
    await seed(env, 3)
    first = await page(env, page_size=1)
    registered = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "other-page-owner@example.com",
            "password": "twelve-character-password",
            "display_name": "Other page owner",
        },
    )
    assert registered.status_code == 201
    other = registered.json()
    env.client.headers["X-NavoX-Account"] = f"{other['workspace']['id']}:{other['id']}"
    response = await env.client.post(ROOT, json={"page_size": 1, "cursor": first["next_cursor"]})
    assert response.status_code == 409
    assert "Conversation" not in response.text
    assert (await page(env))["items"] == []


@pytest.mark.asyncio
async def test_cursor_expires_after_the_original_traversal_deadline(history_env, monkeypatch):
    env = history_env
    await seed(env, 4)
    clock = datetime.now(UTC)

    async def now(database):
        return clock

    monkeypatch.setattr(history, "database_now", now)
    first = await page(env, page_size=1)
    clock += timedelta(minutes=14)
    second = await page(env, page_size=1, cursor=first["next_cursor"])
    clock += timedelta(minutes=2)
    response = await env.client.post(ROOT, json={"page_size": 1, "cursor": second["next_cursor"]})
    assert response.status_code == 409
    assert (await page(env, page_size=1))["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["privacy", "forget"])
async def test_privacy_or_source_erasure_invalidates_pending_pages(history_env, change):
    env = history_env
    rows = await seed(env, 3)
    first = await page(env, page_size=1)
    if change == "privacy":
        current = (await env.client.get("/api/v1/memory/privacy")).json()
        response = await env.client.put(
            "/api/v1/memory/privacy",
            json={**current, "off_the_record": True, "disclosure_accepted": True},
        )
    else:
        response = await env.client.delete(f"/api/v1/memory/history/{rows[-1].id}")
    assert response.status_code in {200, 204}
    assert (
        await env.client.post(ROOT, json={"page_size": 1, "cursor": first["next_cursor"]})
    ).status_code == 409


@pytest.mark.asyncio
async def test_expired_history_is_removed_before_pagination_and_never_decrypted(history_env):
    env = history_env
    rows = await seed(env, 3)
    async with env.factory() as database:
        row = await database.get(MemoryConversation, rows[0].id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    result = await page(env, page_size=25)
    assert [row["id"] for row in result["items"]] == [str(row.id) for row in rows[1:]]
    async with env.factory() as database:
        expired = await database.scalar(
            select(MemoryConversation).where(MemoryConversation.id == rows[0].id)
        )
        assert expired.encrypted_transcript is None and expired.encrypted_summary is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"page_size": 0},
        {"page_size": 101},
        {"query": "private-query-sentinel" + "x" * 201},
        {"cursor": "private-cursor-sentinel" + "x" * 4097},
    ],
)
async def test_page_input_bounds_use_private_sanitized_errors(history_env, params):
    response = await history_env.client.post(ROOT, json=params)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {"detail": "Check this memory request"}
    assert response.request.url.query == b""
    assert "private-query-sentinel" not in response.text
    assert "private-cursor-sentinel" not in response.text


@pytest.mark.asyncio
async def test_history_paging_never_enables_retention_or_creates_privacy(history_env):
    result = await page(history_env)
    assert result == {"items": [], "next_cursor": None, "search_complete": True, "scanned": 0}
    assert (await history_env.client.get("/api/v1/memory/privacy")).json()[
        "history_enabled"
    ] is False
    async with history_env.factory() as database:
        assert (
            await database.get(MemoryPrivacy, (history_env.user_id, history_env.workspace_id))
        ) is None


@pytest.mark.asyncio
async def test_search_text_and_continuation_proof_never_enter_request_urls(history_env):
    env = history_env
    await seed(env, 3)
    query = "private-search-sentinel"
    first = await page(env, query=query, page_size=1)
    cursor = first["next_cursor"]
    response = await env.client.post(ROOT, json={"query": query, "page_size": 1, "cursor": cursor})
    assert response.status_code == 200
    assert response.request.url.query == b""
    assert query not in str(response.request.url) and cursor not in str(response.request.url)
    assert response.json()["items"] == []


@pytest.mark.asyncio
async def test_body_search_requires_the_fresh_browser_origin_and_captured_account(history_env):
    env = history_env
    env.client.headers.pop("Origin")
    response = await env.client.post(ROOT, json={"query": "private-search-sentinel"})
    assert response.status_code == 403
    assert "private-search-sentinel" not in response.text
    env.client.headers["Origin"] = env.settings.web_origin
    env.client.headers.pop("X-NavoX-Account")
    assert (await env.client.post(ROOT, json={})).status_code == 409
    async with env.factory() as database:
        assert (await database.get(MemoryPrivacy, (env.user_id, env.workspace_id))) is None
