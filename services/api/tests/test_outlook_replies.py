"""Synthetic reply reconciliation: no real mailbox or external message sends."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from test_outlook_cleanup_flows import reset
from test_outlook_mail import connect, preview, send_payload
from test_outlook_mail import outlook_env as outlook_env

from navox.connectors.lifecycle import delete_learned_data, disconnect
from navox.db.models import Connection, ConnectorConnection, WorkspaceMembership
from navox.db.outlook import OutlookMailDraft
from navox.outlook import replies, service
from navox.providers.microsoft_mail import MailReply, MailReplyScan


async def sent(env):
    connection_id = await connect(env, send=True)
    draft, _ = await preview(env, connection_id)
    env.provider.receipt_ready = True
    result = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    return connection_id, result.json()


def inbound(env, draft, **changes):
    message_id = f"<navox-{UUID(draft['id']).hex}-{draft['revision']}@mail.navox.app>"
    return replace(
        MailReply(
            id="later-reply",
            conversation_id=env.provider.receipt_conversation,
            sender="teacher@example.com",
            from_address="teacher@example.com",
            recipients=("owner@example.com",),
            received_at=env.provider.receipt_sent_at + timedelta(seconds=10),
            is_draft=False,
            references=(message_id,),
            automated=False,
        ),
        **changes,
    )


async def check(env, draft, *, automatic=False):
    return await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/check-replies",
        json={"automatic": True} if automatic else {},
        headers=env.headers,
    )


@pytest.mark.asyncio
async def test_initial_check_uses_actual_sent_time_and_conversation_and_keeps_send_separate(
    outlook_env,
):
    env = outlook_env
    _, draft = await sent(env)
    assert draft["reply_state"] == "waiting"
    assert draft["reply_scan_complete"] is True
    assert draft["reply_scan_count"] == 0
    assert draft["reply_scan_limit"] == 50
    assert datetime.fromisoformat(draft["sent_at"]) == env.provider.receipt_sent_at
    assert env.provider.reply_calls == 1
    assert env.provider.send_calls == 1
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.reply_conversation_id == env.provider.receipt_conversation
        assert row.reply_conversation_id != env.provider.source.conversation_id
        assert row.reply_recipient_hash == replies.recipient_digest(
            row.mailbox_binding, "teacher@example.com"
        )
        assert "teacher" not in row.reply_recipient_hash


@pytest.mark.asyncio
async def test_later_reply_can_arrive_before_send_verification_and_retires_waiting(outlook_env):
    env = outlook_env
    _, draft = await sent(env)
    response = inbound(env, draft)
    assert response.received_at < datetime.fromisoformat(draft["verified_at"])
    env.provider.reply_scan = MailReplyScan((response,), 1, False, False)
    checked = await check(env, draft)
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result["reply_state"] == "replied"
    assert result["reply_received_at"] == response.received_at.isoformat()
    assert result["status"] == "completed"
    assert result["verified_at"] == draft["verified_at"]
    assert "reply_conversation_id" not in result
    assert "reply_recipient_hash" not in result
    assert "reply_message_id" not in result
    await check(env, draft)
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    [
        ("old", "unknown"),
        ("equal_time", "unknown"),
        ("conversation", "unknown"),
        ("third_party", "unknown"),
        ("alias", "unknown"),
        ("delegate", "unknown"),
        ("other_recipient", "unknown"),
        ("forward", "unknown"),
        ("automated", "unknown"),
        ("draft", "waiting"),
        ("self", "waiting"),
        ("outbound_sender", "waiting"),
    ],
)
async def test_only_exact_reviewed_inbound_reply_counts(outlook_env, change, expected):
    env = outlook_env
    _, draft = await sent(env)
    overrides = {
        "old": {"received_at": env.provider.receipt_sent_at - timedelta(seconds=1)},
        "equal_time": {"received_at": env.provider.receipt_sent_at},
        "conversation": {"conversation_id": "unrelated-thread"},
        "third_party": {"sender": "stranger@example.com", "from_address": "stranger@example.com"},
        "alias": {
            "sender": "teacher+alias@example.com",
            "from_address": "teacher+alias@example.com",
        },
        "delegate": {"sender": "assistant@example.com"},
        "other_recipient": {"recipients": ("someone@example.com",)},
        "forward": {"references": ()},
        "automated": {"automated": True},
        "draft": {"is_draft": True},
        "self": {"from_address": "owner@example.com"},
        "outbound_sender": {"sender": "owner@example.com"},
    }
    env.provider.reply_scan = MailReplyScan(
        (inbound(env, draft, **overrides[change]),), 1, False, False
    )
    response = await check(env, draft)
    assert response.status_code == 200, response.text
    assert response.json()["reply_state"] == expected
    assert response.json()["reply_received_at"] is None
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["truncated", "unreadable", "provider"])
async def test_incomplete_evidence_remains_unknown_even_with_a_plausible_reply(
    outlook_env, failure
):
    env = outlook_env
    _, draft = await sent(env)
    env.provider.reply_scan = MailReplyScan(
        (inbound(env, draft),), 1, failure == "truncated", failure == "unreadable"
    )
    env.provider.fail_replies = failure == "provider"
    response = await check(env, draft)
    assert response.status_code == 200, response.text
    assert response.json()["reply_state"] == "unknown"
    assert response.json()["reply_scan_complete"] is False
    assert response.json()["reply_received_at"] is None
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
async def test_unconfirmed_send_cannot_be_promoted_by_a_reply_check(outlook_env):
    env = outlook_env
    connection_id = await connect(env, send=True)
    draft, _ = await preview(env, connection_id)
    result = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/send", json=send_payload(draft), headers=env.headers
    )
    assert result.json()["status"] == "uncertain"
    result = await check(env, draft)
    assert result.status_code == 409
    assert env.provider.reply_calls == 0
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disconnect", "reset", "demote", "scope"])
async def test_revocation_during_reply_read_cannot_restore_private_metadata(outlook_env, change):
    env = outlook_env
    connection_id, draft = await sent(env)
    env.provider.reply_scan = MailReplyScan((inbound(env, draft),), 1, False, False)

    async def revoke():
        if change == "disconnect":
            response = await env.client.delete(
                f"/api/v1/outlook/connections/{connection_id}", headers=env.headers
            )
            assert response.status_code == 200, response.text
        elif change == "reset":
            response = await reset(env)
            assert response.status_code == 200, response.text
        else:
            async with env.factory() as database:
                if change == "demote":
                    member = await database.get(
                        WorkspaceMembership, (env.workspace_id, env.user_id)
                    )
                    member.role = "member"
                else:
                    native = await database.get(ConnectorConnection, UUID(connection_id))
                    legacy = await database.get(Connection, native.legacy_connection_id)
                    legacy.granted_scopes = []
                await database.commit()

    env.provider.after_replies = revoke
    response = await check(env, draft)
    assert response.status_code in {401, 403, 409}, response.text
    assert "reply_received_at" not in response.text
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.reply_message_id is None
        assert row.reply_state != "replied"
        if change in {"disconnect", "reset"}:
            assert row.sent_at is row.reply_conversation_id is row.reply_recipient_hash is None
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["direct_disconnect", "disconnect", "forget", "reset"])
async def test_privacy_flows_scrub_reply_metadata_but_keep_duplicate_send_fence(
    outlook_env, operation
):
    env = outlook_env
    connection_id, draft = await sent(env)
    env.provider.reply_scan = MailReplyScan((inbound(env, draft),), 1, False, False)
    assert (await check(env, draft)).json()["reply_state"] == "replied"
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        fence = (
            row.mailbox_binding,
            row.receipt_hash,
            row.internet_message_id,
            row.send_request_id,
        )
    if operation == "reset":
        response = await reset(env)
        assert response.status_code == 200, response.text
    elif operation == "direct_disconnect":
        response = await env.client.delete(
            f"/api/v1/outlook/connections/{connection_id}", headers=env.headers
        )
        assert response.status_code == 200, response.text
    else:
        async with env.factory() as database:
            if operation == "forget":
                native = await database.get(ConnectorConnection, UUID(connection_id))
                native.status = native.health_state = "DISCONNECTED"
                await database.commit()
            action = delete_learned_data if operation == "forget" else disconnect
            await action(
                database,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                connection_id=UUID(connection_id),
                request_id=uuid4(),
            )
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.status == "completed"
        assert (
            row.mailbox_binding,
            row.receipt_hash,
            row.internet_message_id,
            row.send_request_id,
        ) == fence
        assert row.reply_state == "not_checked"
        for name in (
            "sent_at",
            "reply_conversation_id",
            "reply_recipient_hash",
            "reply_checked_at",
            "reply_message_id",
            "reply_received_at",
            "reply_scan_count",
            "reply_scan_complete",
        ):
            assert getattr(row, name) is None, name
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
async def test_old_cleared_receipt_backfills_from_exact_mime_and_new_disconnect_scrubs_it(
    outlook_env,
):
    env = outlook_env
    old_id, draft = await sent(env)
    response = await env.client.delete(f"/api/v1/outlook/connections/{old_id}", headers=env.headers)
    assert response.status_code == 200
    async with env.factory() as database:
        await delete_learned_data(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=UUID(old_id),
            request_id=uuid4(),
        )
    new_id = await connect(env)
    assert new_id != old_id
    env.provider.reply_scan = MailReplyScan((inbound(env, draft),), 1, False, False)
    calls = (env.provider.reply_calls, env.provider.receipt_calls, env.provider.refresh_calls)
    automatic = await check(env, draft, automatic=True)
    assert automatic.status_code == 409, automatic.text
    assert (
        env.provider.reply_calls,
        env.provider.receipt_calls,
        env.provider.refresh_calls,
    ) == calls
    checked = await check(env, draft)
    assert checked.status_code == 200, checked.text
    assert checked.json()["reply_state"] == "replied"
    assert checked.json()["connection_id"] == new_id
    assert checked.json()["body"] == ""
    assert checked.json()["content_available"] is False
    async with env.factory() as database:
        await disconnect(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=UUID(new_id),
            request_id=uuid4(),
        )
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.connection_id == UUID(old_id)
        assert row.reply_conversation_id is row.reply_message_id is row.reply_recipient_hash is None
        assert row.reply_state == "not_checked"
        assert row.status == "completed" and row.receipt_hash
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
async def test_reply_check_requires_current_account_and_origin_before_provider_read(outlook_env):
    env = outlook_env
    _, draft = await sent(env)
    calls = env.provider.reply_calls
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/check-replies",
        json={},
        headers={**env.headers, "X-NavoX-Account": f"{env.workspace_id}:{uuid4()}"},
    )
    assert response.status_code == 409
    response = await env.client.post(
        f"/api/v1/outlook/drafts/{draft['id']}/check-replies",
        json={},
        headers={**env.headers, "Origin": "https://untrusted.example"},
    )
    assert response.status_code == 403
    assert env.provider.reply_calls == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["missing_metadata", "cleared", "expired"])
async def test_automatic_checks_cannot_backfill_old_or_cleared_receipts(outlook_env, unavailable):
    env = outlook_env
    _, draft = await sent(env)
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        if unavailable == "missing_metadata":
            row.reply_conversation_id = None
        elif unavailable == "cleared":
            row.encrypted_payload = ""
        else:
            row.payload_expires_at = env.provider.receipt_sent_at - timedelta(days=1)
        await database.commit()
    calls = (env.provider.reply_calls, env.provider.receipt_calls, env.provider.refresh_calls)
    response = await check(env, draft, automatic=True)
    assert response.status_code == 409, response.text
    assert (
        env.provider.reply_calls,
        env.provider.receipt_calls,
        env.provider.refresh_calls,
    ) == calls
    if unavailable == "expired":
        async with env.factory() as database:
            assert (await database.get(OutlookMailDraft, UUID(draft["id"]))).encrypted_payload == ""
    # Explicit review remains available; automatic privacy guards do not destroy the send fence.
    response = await check(env, draft)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
async def test_automatic_reply_check_uses_existing_metadata_without_a_second_receipt_lookup(
    outlook_env,
):
    env = outlook_env
    _, draft = await sent(env)
    assert draft["reply_tracking_ready"] is True
    env.provider.reply_scan = MailReplyScan((inbound(env, draft),), 1, False, False)
    receipt_calls = env.provider.receipt_calls
    response = await check(env, draft, automatic=True)
    assert response.status_code == 200, response.text
    assert response.json()["reply_state"] == "replied"
    assert env.provider.receipt_calls == receipt_calls
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_expiry_during_read_keeps_receipt_writer_transaction_and_auto_does_not_repopulate(
    outlook_env, monkeypatch, automatic
):
    env = outlook_env
    _, draft = await sent(env)
    env.provider.reply_scan = MailReplyScan((inbound(env, draft),), 1, False, False)
    expired = False
    observed_locked_cleanup = False
    original_owned = service.owned_draft

    async def expire_during_read():
        nonlocal expired
        async with env.factory() as database:
            row = await database.get(OutlookMailDraft, UUID(draft["id"]))
            row.payload_expires_at = env.provider.receipt_sent_at - timedelta(days=1)
            await database.commit()
        expired = True

    async def owned_with_transaction_assertion(database, scope, draft_id):
        nonlocal observed_locked_cleanup
        row = await original_owned(database, scope, draft_id)
        if expired and not row.encrypted_payload:
            # Releasing these locks used to let reset/disconnect race the reply writer.
            assert database.in_transaction()
            observed_locked_cleanup = True
        return row

    monkeypatch.setattr(service, "owned_draft", owned_with_transaction_assertion)
    env.provider.after_replies = expire_during_read
    response = await check(env, draft, automatic=automatic)
    assert response.status_code == (409 if automatic else 200), response.text
    assert observed_locked_cleanup
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.encrypted_payload == ""
        assert row.status == "completed" and row.receipt_hash
        assert row.reply_state == ("waiting" if automatic else "replied")
        if automatic:
            assert row.reply_message_id is row.reply_received_at is None
    assert env.provider.send_calls == 1


@pytest.mark.asyncio
async def test_automatic_read_cannot_restore_metadata_after_reset(outlook_env):
    env = outlook_env
    _, draft = await sent(env)
    env.provider.reply_scan = MailReplyScan((inbound(env, draft),), 1, False, False)

    async def reset_during_read():
        result = await reset(env)
        assert result.status_code == 200, result.text

    env.provider.after_replies = reset_during_read
    response = await check(env, draft, automatic=True)
    assert response.status_code == 409, response.text
    async with env.factory() as database:
        row = await database.get(OutlookMailDraft, UUID(draft["id"]))
        assert row.reply_state == "not_checked"
        assert row.encrypted_payload == ""
        assert row.reply_conversation_id is row.reply_recipient_hash is row.reply_message_id is None
        assert row.status == "completed" and row.receipt_hash
