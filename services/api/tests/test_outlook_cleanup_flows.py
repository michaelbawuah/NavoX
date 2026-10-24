"""Shared privacy controls must include Outlook without reopening send attempts."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_outlook_mail import connect, preview, start
from test_outlook_mail import outlook_env as outlook_env

from navox.connectors.lifecycle import delete_learned_data, disconnect
from navox.db.models import AuditEvent, Connection, ConnectorConnection, OAuthAuthorizationAttempt
from navox.db.outlook import OutlookMailDraft
from navox.outlook import cleanup, service


async def seed_draft_states(env, connection_id):
    first, _ = await preview(env, connection_id)
    async with env.factory() as database:
        original = await database.get(OutlookMailDraft, UUID(first["id"]))
        content = service.content_of(env.settings, original)
        identifiers = {"review": original.id}
        for status in ("executing", "uncertain", "completed", "discarded"):
            row = OutlookMailDraft(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                connection_id=UUID(connection_id),
                mailbox_binding=original.mailbox_binding,
                source_message_id=f"synthetic-{status}",
                request_id=uuid4(),
                request_hash="1" * 64,
                revision=1,
                status=status,
                receipt_hash="2" * 64,
                internet_message_id=f"<{status}@synthetic.example>",
            )
            service.save_content(env.settings, row, content)
            database.add(row)
            identifiers[status] = row.id
        connection = await database.get(ConnectorConnection, UUID(connection_id))
        generation = connection.sync_generation
        await database.commit()
    return identifiers, generation


async def reset(env, *, revision=0, request_id=None):
    return await env.client.post(
        "/api/v1/student/data/reset",
        json={"confirmed": True, "revision": revision, "request_id": request_id or str(uuid4())},
        headers=env.headers,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["disconnect", "forget", "reset"])
async def test_shared_privacy_controls_clear_every_outlook_body_state(outlook_env, operation):
    env = outlook_env
    connection_id = await connect(env)
    identifiers, generation = await seed_draft_states(env, connection_id)
    # A used callback can still be in its provider-network gap. It must be
    # invalidated as well as a consent attempt that has never returned.
    await start(env, connection_id=connection_id)
    async with env.factory() as database:
        attempts = list(await database.scalars(select(OAuthAuthorizationAttempt)))
        assert any(item.used_at is None for item in attempts)
        assert any(item.used_at is not None for item in attempts)
        if operation == "forget":
            # Represent a pre-existing disconnected record whose old payloads
            # predate cleanup support; forget must clear them independently.
            connection = await database.get(ConnectorConnection, UUID(connection_id))
            connection.status = connection.health_state = "DISCONNECTED"
            await database.commit()
        if operation != "reset":
            command = disconnect if operation == "disconnect" else delete_learned_data
            await command(
                database,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                connection_id=UUID(connection_id),
                request_id=uuid4(),
            )
    if operation == "reset":
        request_id = str(uuid4())
        response = await reset(env, request_id=request_id)
        assert response.status_code == 200, response.text
        repeated = await reset(env, request_id=request_id)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["reused"] is True
    async with env.factory() as database:
        for previous_status, identifier in identifiers.items():
            row = await database.get(OutlookMailDraft, identifier)
            assert row is not None
            assert row.encrypted_payload == ""
            assert row.status == {"review": "discarded", "executing": "uncertain"}.get(
                previous_status, previous_status
            )
            if previous_status != "review":
                assert row.receipt_hash == "2" * 64
                assert row.internet_message_id == f"<{previous_status}@synthetic.example>"
        attempts = list(await database.scalars(select(OAuthAuthorizationAttempt)))
        assert all(item.purpose == "outlook-cancelled" for item in attempts)
        connection = await database.get(ConnectorConnection, UUID(connection_id))
        assert connection.sync_generation == generation + int(operation != "forget")
        assert connection.status == ("CONNECTED" if operation == "reset" else "DISCONNECTED")
        if operation == "forget":
            provenance = await database.get(Connection, connection.legacy_connection_id)
            assert provenance.external_email is None
            assert provenance.granted_scopes == []
            assert provenance.last_error is None
            assert provenance.external_account_id == f"connector:{connection_id}"
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_stale_student_reset_leaves_outlook_private_data_untouched(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    draft, _ = await preview(env, connection_id)
    async with env.factory() as database:
        generation = (await database.get(ConnectorConnection, UUID(connection_id))).sync_generation
    response = await reset(env, revision=1)
    assert response.status_code == 409, response.text
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.encrypted_payload
        assert row.status == "review"
        assert (
            await database.get(ConnectorConnection, UUID(connection_id))
        ).sync_generation == generation


@pytest.mark.asyncio
async def test_reset_during_outlook_source_read_cannot_restore_a_private_draft(outlook_env):
    env = outlook_env
    connection_id = await connect(env)

    async def reset_during_read():
        response = await reset(env)
        assert response.status_code == 200, response.text

    env.provider.after_message = reset_during_read
    response = await env.client.post(
        "/api/v1/outlook/drafts/preview",
        json={
            "request_id": str(uuid4()),
            "connection_id": connection_id,
            "source_message_id": env.provider.source.id,
            "subject": "Re: Office hours",
            "body": "This draft must not survive the reset.",
        },
        headers=env.headers,
    )
    assert response.status_code == 409, response.text
    async with env.factory() as database:
        assert await database.scalar(select(OutlookMailDraft.id)) is None
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_reset_rejects_a_connection_outside_its_locked_snapshot(outlook_env, monkeypatch):
    env = outlook_env
    connection_id = await connect(env)
    draft, _ = await preview(env, connection_id)

    async def earlier_empty_snapshot(*_args, **_kwargs):
        # Model a connector created after the initial snapshot, before reset
        # acquires the membership/User locks. Do not lock it after User.
        return []

    monkeypatch.setattr(cleanup, "lock_for_reset", earlier_empty_snapshot)
    response = await reset(env)
    assert response.status_code == 409, response.text
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.status == "review" and row.encrypted_payload
        assert (
            await database.scalar(
                select(AuditEvent.id).where(AuditEvent.event_type == "student.data.reset")
            )
            is None
        )
