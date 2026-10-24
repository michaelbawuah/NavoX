"Private body expiry, revocation and ownership use synthetic Outlook fixtures."

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from test_outlook_mail import connect, preview, send_payload
from test_outlook_mail import outlook_env as outlook_env

from navox.db.models import Connection, ConnectorConnection, WorkspaceMembership
from navox.db.outlook import OutlookMailDraft
from navox.outlook import service


@pytest.mark.asyncio
async def test_demotion_during_provider_read_returns_no_mail(outlook_env):
    env = outlook_env
    connection_id = await connect(env)

    async def demote():
        async with env.factory() as db:
            membership = await db.get(WorkspaceMembership, (env.workspace_id, env.user_id))
            membership.role = "member"
            await db.commit()

    env.provider.after_message = demote
    response = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages/source-one", headers=env.headers
    )
    assert response.status_code == 401
    assert "Tuesday" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["status", "scope", "credential"])
async def test_status_and_reads_reflect_current_provenance(outlook_env, change):
    env = outlook_env
    connection_id = await connect(env, send=True)
    async with env.factory() as db:
        native = await db.get(ConnectorConnection, UUID(connection_id))
        legacy = await db.get(Connection, native.legacy_connection_id)
        if change == "status":
            legacy.status = "disconnected"
        elif change == "scope":
            legacy.granted_scopes = []
        else:
            legacy.credential_reference = None
        await db.commit()
    response = await env.client.get("/api/v1/outlook/status", headers=env.headers)
    assert response.json()["connections"][0]["read_enabled"] is False
    assert response.json()["connections"][0]["send_enabled"] is False
    response = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages/source-one", headers=env.headers
    )
    assert response.status_code == 403
    assert env.provider.read_calls == 0


@pytest.mark.asyncio
async def test_saved_body_expiry_is_distinct_from_short_send_approval(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    draft, _ = await preview(env, connection_id)
    async with env.factory() as db:
        stored = await db.get(OutlookMailDraft, UUID(draft["id"]))
        assert stored.payload_expires_at > stored.expires_at
        stored.payload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    response = await env.client.get("/api/v1/outlook/drafts", headers=env.headers)
    assert response.status_code == 200
    assert response.json() == []
    async with env.factory() as db:
        stored = await db.get(OutlookMailDraft, UUID(draft["id"]))
        assert stored.encrypted_payload == ""
        assert stored.status == "discarded"


@pytest.mark.asyncio
@pytest.mark.parametrize("clear", ["disconnect", "expiry"])
async def test_uncertain_content_can_be_cleared_without_losing_receipt_fence(outlook_env, clear):
    env = outlook_env
    connection_id = await connect(env, send=True)
    draft, _ = await preview(env, connection_id)
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "uncertain"
    if clear == "disconnect":
        response = await env.client.delete(
            f"/api/v1/outlook/connections/{connection_id}", headers=env.headers
        )
        assert response.status_code == 200
    else:
        async with env.factory() as db:
            stored = await db.get(OutlookMailDraft, UUID(draft["id"]))
            stored.payload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
    response = await env.client.get("/api/v1/outlook/drafts", headers=env.headers)
    assert response.status_code == 200
    record = response.json()[0]
    assert record["content_available"] is False
    assert record["body"] == record["to"] == record["sender"] == ""
    assert "Tuesday" not in response.text
    async with env.factory() as db:
        stored = await db.get(OutlookMailDraft, UUID(draft["id"]))
        assert stored.encrypted_payload == ""
        assert len(stored.receipt_hash) == 64
        assert stored.status == "uncertain"
    if clear == "disconnect":
        assert await connect(env) == connection_id
    env.provider.receipt_ready = True
    checked = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/check", json={}, headers=env.headers
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "completed"
    assert checked.json()["verified_at"] is not None
    assert checked.json()["body"] == ""
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
async def test_ai_reply_reuses_registered_context_without_saving_or_sending(
    outlook_env, monkeypatch
):
    from test_communication_drafts import DraftRuntime

    env = outlook_env
    connection_id = await connect(env)
    runtime = DraftRuntime()

    async def runtime_factory(settings):
        return runtime

    monkeypatch.setattr(service, "build_runtime", runtime_factory)
    response = await env.client.post(
        "/api/v1/outlook/generate",
        json={
            "connection_id": connection_id,
            "source_message_id": "source-one",
            "instructions": "Draft a brief reply. Ask for missing details.",
        },
        headers=env.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "to": "teacher@example.com",
        "subject": "Friday meeting",
        "body": "Does Friday work for you?",
    }
    assert len(runtime.calls) == 1
    assert env.provider.send_calls == 0
    drafts = await env.client.get("/api/v1/outlook/drafts", headers=env.headers)
    assert drafts.json() == []


@pytest.mark.asyncio
async def test_ai_generation_rechecks_owner_role_after_runtime(outlook_env, monkeypatch):
    from test_communication_drafts import DraftRuntime

    env = outlook_env
    connection_id = await connect(env)

    class DemotingRuntime(DraftRuntime):
        async def execute(self, *args, **kwargs):
            result = await super().execute(*args, **kwargs)
            # Finish the context read transaction, as the actual runtime does
            # across its provider boundary, before independent role withdrawal.
            await kwargs["context_builder"].database.commit()
            async with env.factory() as db:
                membership = await db.get(WorkspaceMembership, (env.workspace_id, env.user_id))
                membership.role = "member"
                await db.commit()
            return result

    async def runtime_factory(settings):
        return DemotingRuntime()

    monkeypatch.setattr(service, "build_runtime", runtime_factory)
    response = await env.client.post(
        "/api/v1/outlook/generate",
        json={
            "connection_id": connection_id,
            "source_message_id": "source-one",
            "instructions": "Reply briefly",
        },
        headers=env.headers,
    )
    assert response.status_code == 401
    assert "Friday" not in response.text
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_forget_and_fresh_connection_preserve_uncertain_send_fence(outlook_env):
    from navox.connectors.lifecycle import delete_learned_data, disconnect

    env = outlook_env
    original_connection = await connect(env, send=True)
    draft, _ = await preview(env, original_connection)
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert response.json()["status"] == "uncertain"
    async with env.factory() as db:
        await disconnect(
            db,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=UUID(original_connection),
            request_id=uuid4(),
        )
        await delete_learned_data(
            db,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=UUID(original_connection),
            request_id=uuid4(),
        )
    status = await env.client.get("/api/v1/outlook/status", headers=env.headers)
    assert status.status_code == 200
    assert status.json()["connections"] == []
    replacement = await connect(env)
    assert replacement != original_connection
    records = await env.client.get("/api/v1/outlook/drafts", headers=env.headers)
    assert records.json()[0]["connection_id"] == replacement
    assert records.json()[0]["body"] == ""
    duplicate = await env.client.post(
        "/api/v1/outlook/drafts/preview",
        json={
            "request_id": str(uuid4()),
            "connection_id": replacement,
            "source_message_id": "source-one",
            "subject": "Duplicate reply",
            "body": "Must not send twice.",
        },
        headers=env.headers,
    )
    assert duplicate.status_code == 409
    candidates = await env.client.get(
        f"/api/v1/outlook/connections/{replacement}/messages", headers=env.headers
    )
    assert candidates.json()["messages"] == []
    env.provider.receipt_ready = True
    checked = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/check", json={}, headers=env.headers
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "completed"
    assert checked.json()["connection_id"] == replacement
    assert checked.json()["body"] == ""
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
async def test_authority_rejects_new_connector_outside_prelocked_snapshot(outlook_env, monkeypatch):
    env = outlook_env
    connection_id = await connect(env)

    async def earlier_empty_snapshot(*_args, **_kwargs):
        # Models a first mailbox inserted after the pre-lock lookup, before
        # authority obtains membership/User. Never lock that row after User.
        return []

    monkeypatch.setattr(service, "lock_for_reset", earlier_empty_snapshot)
    response = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages/source-one",
        headers=env.headers,
    )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "private, no-store"
    assert env.provider.read_calls == 0
    assert env.provider.refresh_calls == 0
