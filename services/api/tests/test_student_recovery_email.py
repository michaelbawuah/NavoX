"""Compose real reviewed email-task acceptance with recovery source fencing."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from test_student_email import accept, analyzed, corrected, email_env  # noqa: F401

from navox.db.models import ConnectorResource
from navox.db.student_planning import StudentTask
from navox.student import service as schedule
from navox.student_email import service as email
from navox.student_recovery import service as recovery
from navox.student_recovery.schemas import RecoveryInput


@pytest.mark.asyncio
async def test_accepted_email_task_uses_reviewed_deadline_without_claiming_a_school_source(
    email_env,  # noqa: F811 - imported pytest fixture
    monkeypatch,
):
    env = email_env
    moment = datetime(2026, 10, 22, 12, tzinfo=UTC)

    async def clock(_database):
        return moment

    monkeypatch.setattr(email, "database_now", clock)
    monkeypatch.setattr(recovery, "database_now", clock)
    review, _ = await corrected(
        env,
        await analyzed(env),
        due_at=(moment - timedelta(days=1)).isoformat(),
        title="An email action I reviewed",
    )
    accepted, _ = await accept(env, review)
    async with env.factory() as database:
        task = await database.get(StudentTask, UUID(accepted["task_id"]))
        assert task.source_item_id is None
        payload = RecoveryInput.model_validate(
            {
                "request_id": str(uuid4()),
                "days": 3,
                "trigger": {
                    "kind": "overdue_task",
                    "task_id": str(task.id),
                    "task_revision": task.revision,
                },
                "work": [
                    {
                        "task_id": str(task.id),
                        "task_revision": task.revision,
                        "remaining_minutes": 20,
                        "priority": 4,
                        "personal_catchup_target": (moment + timedelta(days=2)).isoformat(),
                    }
                ],
            }
        )
        view = await recovery.preview(
            database, env.settings, env.user_id, env.workspace_id, payload
        )
        assert view.work[0].deadline_source == "student_recorded"
        assert view.work[0].official_due_at == moment - timedelta(days=1)
        assert view.work[0].official_deadline_status == "overdue"
        assert view.result.blocks
        (await database.get(ConnectorResource, env.source_id)).content_hash = "0" * 64
        await database.commit()
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError, match="current source") as refusal:
            await recovery.load(
                database, env.settings, env.user_id, env.workspace_id, view.preview_id
            )
        assert refusal.value.status_code == 409
