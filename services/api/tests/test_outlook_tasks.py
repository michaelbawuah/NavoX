"""Selected Outlook email tasks reuse reviewed provenance; fixtures never send mail."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_outlook_mail import connect
from test_outlook_mail import outlook_env as outlook_env

from navox.connectors.lifecycle import delete_learned_data, disconnect
from navox.db.models import ConnectorConnection, ConnectorResource, WorkspaceMembership
from navox.db.outlook import OutlookMailDraft
from navox.db.student_email import EmailTaskSource, StudentEmailReview
from navox.db.student_planning import StudentTask
from navox.student_email import service as email


async def prepare(env, connection_id, *, request_id=None):
    env.settings.student_email_enabled = True
    env.settings.student_planning_enabled = True
    body = {
        "request_id": request_id or str(uuid4()),
        "connection_id": connection_id,
        "source_message_id": env.provider.source.id,
        "confirmed": True,
    }
    response = await env.client.post("/api/v1/outlook/task-reviews", json=body, headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json(), body


async def correct(env, review, **changes):
    payload = {
        "request_id": str(uuid4()),
        "revision": review["revision"],
        "category": "needs_reply",
        "not_important": False,
        "course_id": None,
        "title": "Prepare for office hours",
        "due_at": None,
        "estimated_minutes": 30,
        "confirmed_course": True,
        "confirmed_deadline": True,
        **changes,
    }
    response = await env.client.patch(
        f"/api/v1/outlook/task-reviews/{review['review_id']}", json=payload, headers=env.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def accept(env, review, **changes):
    payload = {
        "request_id": str(uuid4()),
        "revision": review["revision"],
        "source_hash": review["source_hash"],
        "confirmed": True,
        **changes,
    }
    response = await env.client.post(
        f"/api/v1/outlook/task-reviews/{review['review_id']}/accept-task",
        json=payload,
        headers=env.headers,
    )
    return response, payload


@pytest.mark.asyncio
async def test_outlook_task_preview_is_selected_idempotent_and_content_minimized(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    review, payload = await prepare(env, connection_id)
    assert review["actions_executed"] is False
    assert review["correction"]["title"] is None
    assert review["correction"]["estimated_minutes"] is None
    assert review["correction"]["confirmed_course"] is False
    assert review["correction"]["confirmed_deadline"] is False
    repeated = await env.client.post(
        "/api/v1/outlook/task-reviews", json=payload, headers=env.headers
    )
    assert repeated.status_code == 200
    assert repeated.json()["review_id"] == review["review_id"]
    assert env.provider.read_calls == 1
    swapped = await env.client.post(
        "/api/v1/outlook/task-reviews",
        json={**payload, "source_message_id": "different-message"},
        headers=env.headers,
    )
    assert swapped.status_code == 409
    assert env.provider.read_calls == 1
    async with env.factory() as db:
        source = await db.get(ConnectorResource, UUID(review["source_id"]))
        assert source.provider == "microsoft"
        assert source.connector_connection_id == UUID(connection_id)
        assert source.content_hash == review["source_hash"]
        stored_text = json.dumps(
            {"canonical": source.canonical, "metadata": source.provider_metadata}
        )
        assert "Tuesday" not in stored_text and "Office hours" not in stored_text
        assert "teacher@example.com" not in stored_text
        row = await db.get(StudentEmailReview, UUID(review["review_id"]))
        assert "Tuesday" not in (row.encrypted_payload or "")
        assert await db.scalar(select(func.count()).select_from(StudentTask)) == 0
        assert await db.scalar(select(OutlookMailDraft.id)) is None
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_outlook_task_requires_explicit_complete_review_and_exact_acceptance(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    initial, _ = await prepare(env, connection_id)
    missing, _ = await accept(env, initial)
    assert missing.status_code == 422
    review = await correct(env, initial)
    stale, _ = await accept(env, review, revision=initial["revision"])
    assert stale.status_code == 409
    wrong, _ = await accept(env, review, source_hash="0" * 64)
    assert wrong.status_code == 409
    unconfirmed, _ = await accept(env, review, confirmed=False)
    assert unconfirmed.status_code == 422
    response, payload = await accept(env, review)
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    repeated = await env.client.post(
        f"/api/v1/outlook/task-reviews/{review['review_id']}/accept-task",
        json=payload,
        headers=env.headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["task_id"] == task_id and repeated.json()["reused"] is True
    async with env.factory() as db:
        task = await db.get(StudentTask, UUID(task_id))
        assert task.title == "Prepare for office hours"
        assert task.remaining_minutes == 30 and task.course_id is None and task.due_at is None
        link = await db.scalar(select(EmailTaskSource).where(EmailTaskSource.task_id == task.id))
        assert link.connector_id == UUID(connection_id)
        assert link.source_id == UUID(review["source_id"])
        assert link.source_hash == review["source_hash"]
        assert await db.scalar(select(func.count()).select_from(StudentTask)) == 1
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_token_rotation_preserves_review_but_changed_source_requires_review(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    initial, _ = await prepare(env, connection_id)
    review = await correct(env, initial)
    async with env.factory() as db:
        before = (await db.get(ConnectorConnection, UUID(connection_id))).credential_reference
    refreshed = await env.client.get(
        f"/api/v1/outlook/connections/{connection_id}/messages/source-one", headers=env.headers
    )
    assert refreshed.status_code == 200
    async with env.factory() as db:
        after = (await db.get(ConnectorConnection, UUID(connection_id))).credential_reference
        assert before != after
    accepted, _ = await accept(env, review)
    assert accepted.status_code == 200, accepted.text
    task_id = UUID(accepted.json()["task_id"])
    async with env.factory() as db:
        snapshots = await email.email_task_snapshots(
            db, user_id=env.user_id, workspace_id=env.workspace_id, task_ids=[task_id]
        )
        assert snapshots[0]["authorized"] is True and snapshots[0]["needs_review"] is False
    env.provider.source = replace(env.provider.source, body="Please confirm a different deadline.")
    changed, _ = await prepare(env, connection_id)
    assert changed["source_hash"] != review["source_hash"]
    async with env.factory() as db:
        snapshots = await email.email_task_snapshots(
            db, user_id=env.user_id, workspace_id=env.workspace_id, task_ids=[task_id]
        )
        assert snapshots[0]["needs_review"] is True
    changed = await correct(env, changed, estimated_minutes=20)
    duplicate, _ = await accept(env, changed)
    assert duplicate.status_code == 409
    existing = changed["existing_task"]
    updated, _ = await accept(
        env, changed, task_id=existing["id"], task_revision=existing["revision"]
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["task_id"] == str(task_id)
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["outlook_disconnect", "generic_disconnect", "forget", "source_forget", "reset"]
)
async def test_outlook_task_privacy_matches_existing_gmail_policy(outlook_env, operation):
    env = outlook_env
    connection_id = await connect(env)
    initial, _ = await prepare(env, connection_id)
    review = await correct(env, initial)
    accepted, _ = await accept(env, review)
    assert accepted.status_code == 200
    task_id = UUID(accepted.json()["task_id"])
    if operation == "outlook_disconnect":
        response = await env.client.delete(
            f"/api/v1/outlook/connections/{connection_id}", headers=env.headers
        )
        assert response.status_code == 200
    elif operation in {"generic_disconnect", "forget"}:
        async with env.factory() as db:
            await disconnect(
                db,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                connection_id=UUID(connection_id),
                request_id=uuid4(),
            )
            if operation == "forget":
                await delete_learned_data(
                    db,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    connection_id=UUID(connection_id),
                    request_id=uuid4(),
                )
    elif operation == "source_forget":
        # Forget remains possible during a feature rollback.
        env.settings.student_email_enabled = False
        response = await env.client.delete(
            f"/api/v1/outlook/task-sources/{review['source_id']}", headers=env.headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["derived_tasks_erased"] == 1
    else:
        response = await env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
            headers=env.headers,
        )
        assert response.status_code == 200, response.text
    async with env.factory() as db:
        stored = await db.get(StudentEmailReview, UUID(review["review_id"]))
        assert stored.encrypted_payload is None and stored.source_id is None
        if operation in {"outlook_disconnect", "generic_disconnect"}:
            assert await db.get(StudentTask, task_id) is not None
            snapshots = await email.email_task_snapshots(
                db, user_id=env.user_id, workspace_id=env.workspace_id, task_ids=[task_id]
            )
            assert snapshots[0]["authorized"] is False and snapshots[0]["needs_review"] is True
        else:
            assert await db.get(StudentTask, task_id) is None
            assert (
                await db.scalar(
                    select(EmailTaskSource.id).where(EmailTaskSource.task_id == task_id)
                )
                is None
            )
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawal", ["disconnect", "reset", "role"])
async def test_outlook_task_read_cannot_survive_authority_withdrawal(outlook_env, withdrawal):
    env = outlook_env
    connection_id = await connect(env)
    env.settings.student_email_enabled = True

    async def change():
        if withdrawal == "disconnect":
            response = await env.client.delete(
                f"/api/v1/outlook/connections/{connection_id}", headers=env.headers
            )
            assert response.status_code == 200
        elif withdrawal == "reset":
            response = await env.client.post(
                "/api/v1/student/data/reset",
                json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
                headers=env.headers,
            )
            assert response.status_code == 200
        else:
            async with env.factory() as db:
                membership = await db.get(WorkspaceMembership, (env.workspace_id, env.user_id))
                membership.role = "member"
                await db.commit()

    env.provider.after_message = change
    response = await env.client.post(
        "/api/v1/outlook/task-reviews",
        json={
            "request_id": str(uuid4()),
            "connection_id": connection_id,
            "source_message_id": "source-one",
            "confirmed": True,
        },
        headers=env.headers,
    )
    assert response.status_code in {401, 403, 409}
    assert "Tuesday" not in response.text
    async with env.factory() as db:
        assert await db.scalar(select(StudentEmailReview.id)) is None
        assert await db.scalar(select(ConnectorResource.id)) is None
        assert await db.scalar(select(StudentTask.id)) is None
    assert env.provider.send_calls == 0


@pytest.mark.asyncio
async def test_outlook_task_feature_owner_and_acceptance_gates(outlook_env):
    env = outlook_env
    connection_id = await connect(env)
    payload = {
        "request_id": str(uuid4()),
        "connection_id": connection_id,
        "source_message_id": "source-one",
        "confirmed": True,
    }
    disabled = await env.client.post(
        "/api/v1/outlook/task-reviews", json=payload, headers=env.headers
    )
    assert disabled.status_code == 503 and env.provider.read_calls == 0
    env.settings.student_email_enabled = True
    stale = await env.client.post(
        "/api/v1/outlook/task-reviews",
        json=payload,
        headers={**env.headers, "X-NavoX-Account": "other:account"},
    )
    assert stale.status_code == 409 and env.provider.read_calls == 0
    denied = await env.client.post(
        "/api/v1/outlook/task-reviews", json={**payload, "confirmed": False}, headers=env.headers
    )
    assert denied.status_code == 422 and env.provider.read_calls == 0
    initial, _ = await prepare(env, connection_id)
    review = await correct(env, initial)
    env.settings.student_planning_enabled = False
    accepted, _ = await accept(env, review)
    assert accepted.status_code == 503
    assert env.provider.send_calls == 0
