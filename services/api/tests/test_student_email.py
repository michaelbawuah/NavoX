"""Email review is explicit, ephemeral, owner-bound and never send authority."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from navox.api.auth import get_settings
from navox.api.main import create_app
from navox.connectors.builtin.google_gmail import GMAIL_MANIFEST, gmail_resource
from navox.connectors.runtime import resource_hash
from navox.core.settings import Settings
from navox.db.models import (
    Commitment,
    CommitmentSource,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
)
from navox.db.session import get_database_session
from navox.db.student_email import EmailTaskSource, StudentEmailReview
from navox.db.student_planning import StudentTask
from navox.intelligence.contracts import SourceDocument
from navox.providers.google_sources import GMAIL_READ_SCOPE
from navox.student import service as schedule
from navox.student_email import service
from navox.student_email.schemas import AcceptInput, AnalyzeInput, CorrectionInput


class FakeReader:
    def __init__(self, document):
        self.document = document
        self.calls = 0
        self.callback = None

    async def read(self, database, settings, **kwargs):
        assert not database.in_transaction(), "Provider I/O must not retain database locks"
        assert kwargs["external_id"] == self.document.external_id
        self.calls += 1
        if self.callback:
            await self.callback()
        return self.document


@pytest_asyncio.fixture
async def email_env(ai_database):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
    )

    async def database_dependency():
        async with ai_database() as database:
            yield database

    app = create_app()
    app.dependency_overrides[get_database_session] = database_dependency
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        account = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "student-email@example.com",
                "display_name": "Student",
                "password": "twelve-character-password",
            },
        )
        assert account.status_code == 201
        user_id, workspace_id = UUID(account.json()["id"]), UUID(account.json()["workspace"]["id"])
        course = await client.post(
            "/api/v1/student/courses",
            json={
                "code": "CS 2110",
                "name": "Programming",
                "term_start": "2026-08-20",
                "term_end": "2026-12-20",
            },
        )
        assert course.status_code == 201
        connector_id, legacy_id, source_id = uuid4(), uuid4(), uuid4()
        document = SourceDocument(
            id=uuid4(),
            workspace_id=workspace_id,
            provider="google",
            source_type="gmail_message",
            external_id="18a2d9380fe6c4b1",
            subject="CS 2110 lab action",
            content="Please confirm attendance. " + "x" * 500 + "BODY_NOT_RETAINED",
            occurred_at=datetime(2026, 10, 15, 12, tzinfo=UTC),
            retrieved_at=datetime.now(UTC),
            metadata={"status": "active"},
        )
        canonical = gmail_resource(document, connector_id)
        source_id = canonical.resource_id
        async with ai_database() as database:
            legacy = Connection(
                id=legacy_id,
                user_id=user_id,
                workspace_id=workspace_id,
                provider="google",
                external_account_id="student-mail",
                status="active",
                granted_scopes=[GMAIL_READ_SCOPE],
            )
            definition = ConnectorDefinition(
                id=uuid4(),
                connector_key=GMAIL_MANIFEST.id,
                version=GMAIL_MANIFEST.version,
                display_name="Gmail",
                connector_class="OAUTH_API",
                trust_level="NAVOX_FIRST_PARTY",
                manifest=GMAIL_MANIFEST.model_dump(mode="json", by_alias=True),
                active=True,
            )
            database.add_all([legacy, definition])
            await database.flush()
            connector = ConnectorConnection(
                id=connector_id,
                connector_definition_id=definition.id,
                legacy_connection_id=legacy_id,
                user_id=user_id,
                workspace_id=workspace_id,
                provider="google",
                external_account_id="student-mail",
                status="CONNECTED",
                health_state="CONNECTED",
                authorized_capabilities=["communication.messages.read"],
                provider_capabilities=["communication.messages.read"],
            )
            database.add(connector)
            await database.flush()
            database.add(
                ConnectorResource(
                    id=source_id,
                    workspace_id=workspace_id,
                    connector_connection_id=connector_id,
                    provider="google",
                    resource_type="communication.message",
                    external_id=document.external_id,
                    content_hash=resource_hash(canonical),
                    canonical={"status": "active", "content_persisted": False},
                    provider_metadata={"resource_hash": resource_hash(canonical)},
                    retrieved_at=document.retrieved_at,
                )
            )
            await database.commit()
        yield SimpleNamespace(
            factory=ai_database,
            settings=settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=UUID(course.json()["id"]),
            source_id=source_id,
            source_hash=resource_hash(canonical),
            connector_id=connector_id,
            legacy_id=legacy_id,
            reader=FakeReader(document),
            app=app,
            client=client,
        )


async def analyzed(env, request_id=None):
    async with env.factory() as database:
        return await service.analyze(
            database,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            payload=AnalyzeInput(
                request_id=request_id or uuid4(),
                source_id=env.source_id,
                source_hash=env.source_hash,
            ),
            reader=env.reader,
        )


async def corrected(env, review, **updates):
    payload = CorrectionInput.model_validate(
        {
            "request_id": str(uuid4()),
            "revision": review["revision"],
            "category": "needs_reply",
            "not_important": False,
            "course_id": str(env.course_id),
            "title": "Confirm lab attendance",
            "due_at": "2026-11-01T17:00:00Z",
            "estimated_minutes": 20,
            "confirmed_course": True,
            "confirmed_deadline": True,
            **updates,
        }
    )
    async with env.factory() as database:
        result = await service.correct(
            database,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            review_id=UUID(review["review_id"]),
            payload=payload,
        )
    return result, payload


async def accept(env, review, request_id=None):
    payload = AcceptInput(
        request_id=request_id or uuid4(),
        revision=review["revision"],
        source_hash=review["source_hash"],
        confirmed=True,
    )
    async with env.factory() as database:
        value = await service.accept_task(
            database,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            review_id=UUID(review["review_id"]),
            payload=payload,
        )
    return value, payload


@pytest.mark.asyncio
async def test_explicit_analyze_is_idempotent_ephemeral_encrypted_and_not_send_authority(email_env):
    env = email_env
    request_id = uuid4()
    review = await analyzed(env, request_id)
    replay = await analyzed(env, request_id)
    assert review["review_id"] == replay["review_id"] and env.reader.calls == 1
    assert review["actions_executed"] is False and review["reply_handoff"] is None
    assert review["correction"]["course_id"] is None
    async with env.factory() as database:
        row = await database.get(StudentEmailReview, UUID(review["review_id"]))
        assert (
            "attendance" not in row.encrypted_payload
            and "BODY_NOT_RETAINED" not in row.encrypted_payload
        )
        data = service.reveal(row, env.settings)
        assert "BODY_NOT_RETAINED" not in str(data) and "recipients" not in data
        assert "Please confirm attendance" not in str(row.__dict__)
        source = await database.get(ConnectorResource, env.source_id)
        assert source.canonical == {"status": "active", "content_persisted": False}
        assert await database.scalar(select(func.count()).select_from(StudentTask)) == 0


@pytest.mark.asyncio
async def test_current_source_hash_and_live_revocation_fence_slow_analyze(email_env):
    env = email_env

    async def revoke():
        async with env.factory() as database:
            (await database.get(ConnectorConnection, env.connector_id)).authorized_capabilities = []
            await database.commit()

    env.reader.callback = revoke
    with pytest.raises((service.EmailError, service.coursework.CourseworkError)):
        await analyzed(env)
    async with env.factory() as database:
        row = await database.scalar(select(StudentEmailReview))
        assert row.encrypted_payload is None


@pytest.mark.asyncio
async def test_changed_provider_document_is_rejected_without_persisting_body(email_env):
    env = email_env
    env.reader.document = env.reader.document.model_copy(update={"subject": "CHANGED-PRIVATE-TEXT"})
    with pytest.raises(service.EmailError, match="changed"):
        await analyzed(env)
    async with env.factory() as database:
        assert (await database.scalar(select(StudentEmailReview))).encrypted_payload is None


@pytest.mark.asyncio
async def test_corrections_are_revision_fenced_not_important_and_require_explicit_task_fields(
    email_env,
):
    env = email_env
    review = await analyzed(env)
    with pytest.raises(service.EmailError):
        await accept(env, review)
    corrected_review, payload = await corrected(env, review)
    async with env.factory() as database:
        same = await service.correct(
            database,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            review_id=UUID(review["review_id"]),
            payload=payload,
        )
        assert same["revision"] == corrected_review["revision"]
    with pytest.raises(service.EmailError):
        await corrected(env, review, title="Stale edit")
    ignored, _ = await corrected(env, corrected_review, not_important=True)
    assert ignored["correction"]["category"] == "ignore"
    with pytest.raises(service.EmailError):
        await accept(env, ignored)


@pytest.mark.asyncio
async def test_accepted_email_task_has_generic_live_source_fence_and_no_calendar_write(email_env):
    env = email_env
    review, _ = await corrected(env, await analyzed(env))
    receipt, payload = await accept(env, review)
    task_id = UUID(receipt["task_id"])
    async with env.factory() as database:
        replay = await service.accept_task(
            database,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            review_id=UUID(review["review_id"]),
            payload=payload,
        )
        assert replay["reused"] and replay["available"]
        task = await database.get(StudentTask, task_id)
        assert task.course_id == env.course_id and task.estimated_minutes == 20
        assert task.source_item_id is None and task.title == "Confirm lab attendance"
        current = await service.email_task_snapshots(
            database, user_id=env.user_id, workspace_id=env.workspace_id, task_ids=[task_id]
        )
        assert current[0]["authorized"] is True and not current[0]["needs_review"]
        source = await database.get(ConnectorResource, env.source_id)
        source.content_hash = "b" * 64
        await database.commit()
        changed = await service.email_task_snapshots(
            database, user_id=env.user_id, workspace_id=env.workspace_id, task_ids=[task_id]
        )
        assert changed[0]["authorized"] is True and changed[0]["needs_review"] is True
        (await database.get(ConnectorConnection, env.connector_id)).health_state = "PAUSED"
        await database.commit()
        revoked = await service.email_task_snapshots(
            database, user_id=env.user_id, workspace_id=env.workspace_id, task_ids=[task_id]
        )
        assert revoked[0]["authorized"] is False and revoked[0]["needs_review"] is True


@pytest.mark.asyncio
async def test_erasure_quota_flag_independent_tombstones_and_source_derived_copy_cleanup(
    email_env, monkeypatch
):
    env = email_env
    request_id = uuid4()
    review, _ = await corrected(env, await analyzed(env, request_id))
    receipt, _ = await accept(env, review)
    monkeypatch.setattr(service, "MAX_RECEIPTS", 1)
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        erased = await service.forget_email_data(
            database, user_id=env.user_id, workspace_id=env.workspace_id, source_id=env.source_id
        )
        await database.commit()
        assert erased["derived_tasks_erased"] == 1
        assert await database.get(StudentTask, UUID(receipt["task_id"])) is None
        assert await database.scalar(select(func.count()).select_from(EmailTaskSource)) == 0
        row = await database.get(StudentEmailReview, UUID(review["review_id"]))
        assert row.encrypted_payload is None and row.source_id is None and row.source_hash is None
    with pytest.raises(service.EmailError) as error:
        await analyzed(env, request_id)
    assert error.value.status_code == 410 and env.reader.calls == 1


@pytest.mark.asyncio
async def test_expiry_cleanup_removes_ciphertext_and_retains_content_free_replay_fence(email_env):
    env = email_env
    review = await analyzed(env)
    async with env.factory() as database:
        row = await database.get(StudentEmailReview, UUID(review["review_id"]))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await database.commit()
        with pytest.raises(service.EmailError) as error:
            await service.read_review(
                database,
                env.settings,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                review_id=UUID(review["review_id"]),
            )
        assert error.value.status_code == 410
        assert await service.cleanup_email_reviews(database) == 1
        await database.commit()
        assert row.encrypted_payload is None and row.payload_hash and row.source_id is None


@pytest.mark.asyncio
async def test_source_reset_during_selected_message_read_cannot_restore_erased_review(email_env):
    env = email_env

    async def forget():
        async with env.factory() as database:
            await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
            await service.forget_email_data(
                database,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                source_id=env.source_id,
            )
            await database.commit()

    env.reader.callback = forget
    with pytest.raises(service.EmailError) as error:
        await analyzed(env)
    assert error.value.status_code == 410
    async with env.factory() as database:
        row = await database.scalar(select(StudentEmailReview))
        assert row.encrypted_payload is None and row.source_id is None


@pytest.mark.asyncio
async def test_owner_scoping_rejects_foreign_review_source_and_course_before_private_reads(
    email_env,
):
    env = email_env
    review = await analyzed(env)
    env.client.cookies.clear()
    response = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "other-student-email@example.com",
            "display_name": "Other",
            "password": "twelve-character-password",
        },
    )
    other_user = UUID(response.json()["id"])
    other_workspace = UUID(response.json()["workspace"]["id"])
    course = await env.client.post(
        "/api/v1/student/courses",
        json={
            "code": "OTHER",
            "name": "Foreign course",
            "term_start": "2026-08-20",
            "term_end": "2026-12-20",
        },
    )
    async with env.factory() as database:
        with pytest.raises(service.EmailError) as hidden:
            await service.read_review(
                database,
                env.settings,
                user_id=other_user,
                workspace_id=other_workspace,
                review_id=UUID(review["review_id"]),
            )
        assert hidden.value.status_code == 404
    async with env.factory() as database:
        with pytest.raises(service.coursework.CourseworkError):
            await service.analyze(
                database,
                env.settings,
                user_id=other_user,
                workspace_id=other_workspace,
                payload=AnalyzeInput(
                    request_id=uuid4(), source_id=env.source_id, source_hash=env.source_hash
                ),
                reader=env.reader,
            )
    with pytest.raises(schedule.StudentError):
        await corrected(env, review, course_id=course.json()["id"])
    assert env.reader.calls == 1


@pytest.mark.asyncio
async def test_review_limit_and_unconfigured_encryption_stop_before_provider_read_but_allow_erase(
    email_env, monkeypatch
):
    env = email_env
    review = await analyzed(env)
    monkeypatch.setattr(service, "MAX_ACTIVE", 1)
    with pytest.raises(service.EmailError) as quota:
        await analyzed(env)
    assert quota.value.status_code == 429 and env.reader.calls == 1
    async with env.factory() as database:
        erased = await service.erase_review(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            review_id=UUID(review["review_id"]),
            revision=1,
        )
        assert erased["erased"]
    env.settings = env.settings.model_copy(
        update={"connector_secret_encryption_key": None, "google_token_encryption_key": None}
    )
    with pytest.raises(service.EmailError) as unconfigured:
        await analyzed(env)
    assert unconfigured.value.status_code == 503 and env.reader.calls == 1


@pytest.mark.asyncio
async def test_expiry_cleanup_skips_tombstones_so_later_ciphertexts_are_not_starved(email_env):
    env = email_env
    await analyzed(env)
    await analyzed(env)
    async with env.factory() as database:
        rows = list(await database.scalars(select(StudentEmailReview)))
        for row in rows:
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await database.commit()
        assert await service.cleanup_email_reviews(database, limit=1) == 1
        await database.commit()
        assert await service.cleanup_email_reviews(database, limit=1) == 1
        await database.commit()
        assert all(row.encrypted_payload is None for row in rows)
        assert await service.cleanup_email_reviews(database, limit=1) == 0


@pytest.mark.asyncio
async def test_http_review_to_explicit_task_then_disabled_review_erasure(email_env, monkeypatch):
    from navox.api import student_email as api

    env = email_env
    env.settings.__dict__["student_email_enabled"] = True
    env.app.include_router(api.router, prefix="/api/v1")
    monkeypatch.setattr(api, "get_email_reader", lambda: env.reader)
    response = await env.client.post(
        "/api/v1/student/email/analyze",
        json={
            "request_id": str(uuid4()),
            "source_id": str(env.source_id),
            "source_hash": env.source_hash,
        },
    )
    assert response.status_code == 200, response.text
    review = response.json()
    response = await env.client.put(
        f"/api/v1/student/email/reviews/{review['review_id']}",
        json={
            "request_id": str(uuid4()),
            "revision": review["revision"],
            "category": "needs_reply",
            "not_important": False,
            "course_id": str(env.course_id),
            "title": "Confirm attendance",
            "due_at": None,
            "estimated_minutes": 10,
            "confirmed_course": True,
            "confirmed_deadline": True,
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    receipt = await env.client.post(
        f"/api/v1/student/email/reviews/{review['review_id']}/accept-task",
        json={
            "request_id": str(uuid4()),
            "revision": updated["revision"],
            "source_hash": updated["source_hash"],
            "confirmed": True,
        },
    )
    assert receipt.status_code == 200 and receipt.json()["available"]
    env.settings.__dict__["student_email_enabled"] = False
    deleted = await env.client.request(
        "DELETE",
        f"/api/v1/student/email/reviews/{review['review_id']}",
        json={"revision": updated["revision"]},
    )
    assert deleted.status_code == 200 and deleted.json()["erased"]
    assert env.reader.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_truncated", [False, True])
async def test_partial_message_never_becomes_a_confident_deadline_proposal(
    email_env, provider_truncated
):
    env = email_env
    content = "Due 2026-11-01T17:00Z."
    metadata = {"status": "active"}
    if provider_truncated:
        metadata["content_truncated"] = True
    else:
        content += "x" * 12000 + "Due 2026-11-02T17:00Z."
    document = env.reader.document.model_copy(update={"content": content, "metadata": metadata})
    env.reader.document = document
    env.source_hash = resource_hash(gmail_resource(document, env.connector_id))
    async with env.factory() as database:
        (await database.get(ConnectorResource, env.source_id)).content_hash = env.source_hash
        await database.commit()
    review = await analyzed(env)
    assert review["content_truncated"]
    assert review["classification"]["proposed_deadline"] is None
    assert review["classification"]["ambiguities"]
    assert review["correction"]["due_at"] is None


@pytest.mark.asyncio
async def test_subject_at_provider_header_cap_never_implies_complete_deadline_evidence(email_env):
    env = email_env
    subject = "Due 2026-11-01T17:00Z. "
    subject += "x" * (2000 - len(subject))
    document = env.reader.document.model_copy(update={"subject": subject})
    env.reader.document = document
    env.source_hash = resource_hash(gmail_resource(document, env.connector_id))
    async with env.factory() as database:
        (await database.get(ConnectorResource, env.source_id)).content_hash = env.source_hash
        await database.commit()
    review = await analyzed(env)
    assert review["content_truncated"]
    assert review["classification"]["proposed_deadline"] is None
    assert review["classification"]["ambiguities"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "credential", "account", "scopes"])
async def test_source_authority_cannot_change_and_resume_during_message_read(email_env, change):
    env = email_env

    async def change_authority():
        async with env.factory() as database:
            connector = await database.get(ConnectorConnection, env.connector_id)
            legacy = await database.get(Connection, env.legacy_id)
            if change == "generation":
                connector.sync_generation += 1
            elif change == "account":
                legacy.external_account_id = "different-mailbox"
            elif change == "scopes":
                legacy.granted_scopes = [GMAIL_READ_SCOPE, "new-scope"]
            else:
                credential = ConnectionCredential(
                    id=uuid4(), encrypted_refresh_token="unused-test-value"
                )
                database.add(credential)
                await database.flush()
                legacy.credential_reference = credential.id
            await database.commit()

    env.reader.callback = change_authority
    with pytest.raises(service.EmailError, match="authority changed"):
        await analyzed(env)
    async with env.factory() as database:
        assert (await database.scalar(select(StudentEmailReview))).encrypted_payload is None


@pytest.mark.asyncio
async def test_accepted_task_can_refresh_changed_source_only_after_new_exact_review(email_env):
    env = email_env
    first_review, _ = await corrected(env, await analyzed(env))
    accepted, _ = await accept(env, first_review)
    task_id = UUID(accepted["task_id"])
    async with env.factory() as database:
        task = await database.get(StudentTask, task_id)
        task.actual_minutes = 5
        task.remaining_minutes = 15
        legacy = await database.get(Connection, env.legacy_id)
        legacy.external_account_id = "refreshed-authorized-mailbox"
        await database.commit()
        stale = await service.email_task_snapshots(
            database, user_id=env.user_id, workspace_id=env.workspace_id, task_ids=[task_id]
        )
        assert stale[0]["needs_review"]
    review, _ = await corrected(
        env, await analyzed(env), title="Confirm updated lab attendance", estimated_minutes=30
    )
    with pytest.raises(service.EmailError):
        await accept(env, review)
    payload = AcceptInput(
        request_id=uuid4(),
        revision=review["revision"],
        source_hash=review["source_hash"],
        confirmed=True,
        task_id=task_id,
        task_revision=1,
    )
    async with env.factory() as database:
        refreshed = await service.accept_task(
            database,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            review_id=UUID(review["review_id"]),
            payload=payload,
        )
        assert refreshed["task_id"] == str(task_id)
        task = await database.get(StudentTask, task_id)
        assert task.revision == 2 and task.actual_minutes == 5
        assert task.remaining_minutes == 30 and task.estimated_minutes == 35
        assert task.title == "Confirm updated lab attendance"
        current = await service.email_task_snapshots(
            database, user_id=env.user_id, workspace_id=env.workspace_id, task_ids=[task_id]
        )
        assert current[0]["needs_review"] is False
        assert await database.scalar(select(func.count()).select_from(StudentTask)) == 1


@pytest.mark.asyncio
async def test_ordinary_sync_generation_and_live_degraded_health_do_not_stale_accepted_task(
    email_env,
):
    env = email_env
    review, _ = await corrected(env, await analyzed(env))
    accepted, _ = await accept(env, review)
    async with env.factory() as database:
        connector = await database.get(ConnectorConnection, env.connector_id)
        connector.sync_generation += 1
        connector.status = "DEGRADED"
        connector.health_state = "DEGRADED"
        await database.commit()
        snapshots = await service.email_task_snapshots(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            task_ids=[UUID(accepted["task_id"])],
        )
        assert snapshots[0]["authorized"] and not snapshots[0]["needs_review"]
        # A still-open source review retains the stricter in-flight generation fence.
        with pytest.raises(service.EmailError, match="authority changed"):
            await service.read_review(
                database,
                env.settings,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                review_id=UUID(review["review_id"]),
            )


@pytest.mark.asyncio
async def test_reply_handoff_uses_existing_owned_inbox_review_route_without_new_send_authority(
    email_env,
):
    env = email_env
    item_id, reference_id = uuid4(), uuid4()
    async with env.factory() as database:
        database.add(
            Commitment(
                id=item_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                commitment_type="reply",
                title="Confirm lab attendance",
                status="confirmed",
                dedupe_key=uuid4().hex,
            )
        )
        await database.flush()
        database.add(
            CommitmentSource(
                id=reference_id,
                commitment_id=item_id,
                connection_id=env.legacy_id,
                provider="google",
                source_type="gmail_message",
                external_resource_id=env.reader.document.external_id,
            )
        )
        await database.commit()
    review = await analyzed(env)
    assert review["reply_handoff"]["path"] == f"/inbox?reply={item_id}#drafts"
    assert review["reply_handoff"]["source_id"] == str(reference_id)
    assert "recipient" not in review["reply_handoff"] and "body" not in review["reply_handoff"]
    assert review["actions_executed"] is False


@pytest.mark.asyncio
async def test_review_response_rechecks_authority_after_commit_before_returning_evidence(
    email_env, monkeypatch
):
    env = email_env
    original = service.view

    async def changed_before_return(*args, **kwargs):
        async with env.factory() as database:
            connector = await database.get(ConnectorConnection, env.connector_id)
            connector.sync_generation += 1
            await database.commit()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "view", changed_before_return)
    with pytest.raises(service.EmailError, match="authority changed"):
        await analyzed(env)


@pytest.mark.asyncio
async def test_shared_planner_hash_eligibility_and_task_review_recheck_email_source(
    email_env, monkeypatch
):
    from navox.student_planning import service as planning
    from navox.student_planning.schemas import AcceptInput as PlanAccept
    from navox.student_planning.schemas import PreviewInput

    env = email_env
    moment = datetime(2026, 10, 19, 7, tzinfo=UTC)

    async def clock(database):
        return moment

    monkeypatch.setattr(service, "database_now", clock)
    monkeypatch.setattr(planning, "database_now", clock)
    review, _ = await corrected(env, await analyzed(env))
    receipt, _ = await accept(env, review)
    task_id = UUID(receipt["task_id"])
    async with env.factory() as database:
        proposal = await planning.preview(
            database, env.user_id, env.workspace_id, PreviewInput(days=2), now=moment
        )
        accepted = await planning.accept(
            database,
            env.user_id,
            env.workspace_id,
            PlanAccept(
                preview_id=proposal.preview_id,
                preview_hash=proposal.preview_hash,
                request_id=uuid4(),
                confirmed=True,
            ),
            now=moment,
        )
        assert not accepted.needs_review
        source_states = await planning.current_sources(
            database,
            env.user_id,
            env.workspace_id,
            await planning.tasks(database, env.user_id, env.workspace_id),
        )
        assert source_states[0]["source_kind"] == "email"
        (await database.get(ConnectorResource, env.source_id)).content_hash = "b" * 64
        await database.commit()
        task_views = await planning.tasks(
            database, env.user_id, env.workspace_id, include_source_review=True
        )
        selected = next(task for task in task_views if task.id == task_id)
        assert selected.source_needs_review and not selected.source_reviewable
        assert "Email actions" in selected.source_review_notice
        assert (
            await planning.eligible_tasks(database, env.user_id, env.workspace_id, task_views) == []
        )
        assert (await planning.plan_view(database, env.user_id, env.workspace_id)).needs_review
        with pytest.raises(schedule.StudentError) as stale:
            await planning.preview(
                database, env.user_id, env.workspace_id, PreviewInput(days=2), now=moment
            )
        assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_shared_disconnect_and_connector_erasure_remove_derived_email_copies(email_env):
    from navox.connectors import lifecycle

    env = email_env
    review, _ = await corrected(env, await analyzed(env))
    receipt, _ = await accept(env, review)
    async with env.factory() as database:
        manual = StudentTask(
            id=uuid4(),
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            title="Independent manual work",
            estimated_minutes=10,
            remaining_minutes=10,
            actual_minutes=0,
            difficulty=3,
            priority=3,
            course_weight=1,
            status="active",
            revision=1,
        )
        database.add(manual)
        await database.commit()
        await lifecycle.disconnect(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=env.legacy_id,
            request_id=uuid4(),
        )
        row = await database.get(StudentEmailReview, UUID(review["review_id"]))
        assert row.encrypted_payload is None and row.source_id is None
        # Disconnect preserves the explicitly saved task, whose live source is now invalid.
        assert await database.get(StudentTask, UUID(receipt["task_id"])) is not None
        snapshots = await service.email_task_snapshots(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            task_ids=[UUID(receipt["task_id"])],
        )
        assert snapshots[0]["needs_review"] and not snapshots[0]["authorized"]
        await lifecycle.delete_learned_data(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=env.legacy_id,
            request_id=uuid4(),
        )
        assert await database.get(ConnectorResource, env.source_id) is None
        assert await database.get(StudentTask, UUID(receipt["task_id"])) is None
        assert await database.scalar(select(func.count()).select_from(EmailTaskSource)) == 0
        assert await database.get(StudentTask, manual.id) is not None
        assert row.encrypted_payload is None and row.source_hash is None
    assert env.reader.calls == 1


@pytest.mark.asyncio
async def test_shared_full_student_reset_erases_email_while_flag_off_and_keeps_source_provider_data(
    email_env,
):
    env = email_env
    request_id = uuid4()
    review, _ = await corrected(env, await analyzed(env, request_id))
    receipt, _ = await accept(env, review)
    env.settings.student_email_enabled = False
    response = await env.client.post(
        "/api/v1/student/data/reset",
        json={"revision": 0, "request_id": str(uuid4()), "confirmed": True},
    )
    assert response.status_code == 200 and response.json()["cleared"], response.text
    async with env.factory() as database:
        row = await database.get(StudentEmailReview, UUID(review["review_id"]))
        assert (
            row.encrypted_payload is None and row.source_id is None and row.authority_hash is None
        )
        assert await database.get(StudentTask, UUID(receipt["task_id"])) is None
        assert await database.scalar(select(func.count()).select_from(EmailTaskSource)) == 0
        assert await database.get(ConnectorResource, env.source_id) is not None
        assert await database.get(Connection, env.legacy_id) is not None
    with pytest.raises(service.EmailError) as erased:
        await analyzed(env, request_id)
    assert erased.value.status_code == 410 and env.reader.calls == 1


@pytest.mark.asyncio
async def test_fresh_review_exposes_only_the_exact_owned_existing_task_for_refresh(email_env):
    env = email_env
    review, _ = await corrected(env, await analyzed(env))
    receipt, _ = await accept(env, review)
    fresh = await analyzed(env)
    assert fresh["task_id"] is None
    assert fresh["existing_task"] == {
        "id": receipt["task_id"],
        "revision": 1,
        "title": "Confirm lab attendance",
        "course_id": str(env.course_id),
        "status": "active",
        "remaining_minutes": 20,
        "actual_minutes": 0,
    }
    async with env.factory() as database:
        task = await database.get(StudentTask, UUID(receipt["task_id"]))
        task.source_identity = service.digest({"student_email_source": str(uuid4())})
        await database.commit()
    assert (await analyzed(env))["existing_task"] is None


@pytest.mark.asyncio
async def test_connector_forget_erases_orphaned_source_copies_using_durable_connector_identity(
    email_env,
):
    from navox.connectors import lifecycle

    env = email_env
    review, _ = await corrected(env, await analyzed(env))
    receipt, _ = await accept(env, review)
    await analyzed(env)  # A second encrypted review can outlive the source locator too.
    async with env.factory() as database:
        manual = StudentTask(
            id=uuid4(),
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            title="Keep independent manual work",
            estimated_minutes=15,
            remaining_minutes=15,
            actual_minutes=0,
            difficulty=3,
            priority=3,
            course_weight=1,
            status="active",
            revision=1,
        )
        database.add(manual)
        reviews = list(await database.scalars(select(StudentEmailReview)))
        link = await database.scalar(select(EmailTaskSource))
        assert all(row.connector_id == env.connector_id for row in reviews)
        assert link.connector_id == env.connector_id
        # Model the PostgreSQL SET NULL resource cascade, including databases restored from
        # an older eraser that removed a source before running the new private-copy hook.
        for row in reviews:
            row.source_id = None
        link.source_id = None
        source = await database.get(ConnectorResource, env.source_id)
        await database.delete(source)
        await database.commit()
        assert any(row.encrypted_payload is not None for row in reviews)
        await lifecycle.disconnect(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=env.legacy_id,
            request_id=uuid4(),
        )
        assert all(row.encrypted_payload is None for row in reviews)
        assert await database.get(StudentTask, UUID(receipt["task_id"])) is not None
        await lifecycle.delete_learned_data(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=env.legacy_id,
            request_id=uuid4(),
        )
        assert await database.get(StudentTask, UUID(receipt["task_id"])) is None
        assert await database.scalar(select(func.count()).select_from(EmailTaskSource)) == 0
        assert await database.get(StudentTask, manual.id) is not None
        assert all(
            row.encrypted_payload is None and row.source_hash is None and row.connector_id is None
            for row in reviews
        )
