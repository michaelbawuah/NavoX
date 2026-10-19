"""Real PostgreSQL lock interleavings; every selected-message read is synthetic.

NAVOX_CONNECTOR_TEST_DSN selects the existing isolated-schema ai_database fixture.
SQLite deliberately skips: it cannot establish these User row-lock guarantees.
"""

import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from test_student_email import accept, analyzed, corrected, email_env  # noqa: F401

from navox.db.models import User
from navox.db.student_email import EmailTaskSource, StudentEmailReview
from navox.db.student_planning import StudentStudyBlock, StudentTask
from navox.student import service as schedule
from navox.student_email import service
from navox.student_email.schemas import AcceptInput, AnalyzeInput


@pytest_asyncio.fixture
async def postgres_email_env(email_env):  # noqa: F811 - imported shared pytest fixture
    if email_env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL User row locks and pg_blocking_pids")
    return email_env


async def wait_for_real_lock_waits(observer, pids: list[int], holder_pid: int) -> None:
    """Prove SQL reached a blocking lock, rather than assuming task scheduling order."""
    async with asyncio.timeout(10):
        while True:
            blockers = [
                await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid})
                for pid in pids
            ]
            if all(blockers) and any(holder_pid in values for values in blockers):
                return
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_postgres_same_review_accept_waits_refreshes_and_replays_one_task(
    postgres_email_env, monkeypatch
):
    env = postgres_email_env
    review, _ = await corrected(env, await analyzed(env))
    review_id = UUID(review["review_id"])
    payload = AcceptInput(
        request_id=uuid4(),
        revision=review["revision"],
        source_hash=review["source_hash"],
        confirmed=True,
    )
    original_owner = schedule.owner
    started: dict[int, asyncio.Event] = {}

    async def observe_owner(database, *args, **kwargs):
        if kwargs.get("lock") and id(database) in started:
            started[id(database)].set()
        return await original_owner(database, *args, **kwargs)

    monkeypatch.setattr(schedule, "owner", observe_owner)
    async with (
        env.factory() as holder,
        env.factory() as first,
        env.factory() as second,
        env.factory() as observer,
    ):
        # Strong references retain stale ORM identity-map values until both writers finish.
        old_first = await first.get(StudentEmailReview, review_id)
        old_second = await second.get(StudentEmailReview, review_id)
        assert old_first.accepted_task_id is old_second.accepted_task_id is None
        assert old_first.revision == old_second.revision == review["revision"]
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        await original_owner(holder, env.user_id, env.workspace_id, lock=True)
        started[id(first)], started[id(second)] = asyncio.Event(), asyncio.Event()

        async def write(database):
            return await service.accept_task(
                database,
                env.settings,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                review_id=review_id,
                payload=payload,
            )

        writes = [asyncio.create_task(write(first)), asyncio.create_task(write(second))]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started.values())), 10
            )
            await wait_for_real_lock_waits(observer, [first_pid, second_pid], holder_pid)
            assert not any(task.done() for task in writes)
            await holder.commit()
            outcomes = await asyncio.wait_for(asyncio.gather(*writes), 15)
        finally:
            await holder.rollback()
            for task in writes:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*writes, return_exceptions=True)
    assert outcomes[0]["task_id"] == outcomes[1]["task_id"]
    assert sorted(outcome["reused"] for outcome in outcomes) == [False, True]
    assert all(outcome["available"] and not outcome["actions_executed"] for outcome in outcomes)
    task_id = UUID(outcomes[0]["task_id"])
    async with env.factory() as database:
        task = await database.get(StudentTask, task_id)
        assert (task.user_id, task.workspace_id) == (env.user_id, env.workspace_id)
        assert await database.scalar(select(func.count()).select_from(StudentTask)) == 1
        sources = list(await database.scalars(select(EmailTaskSource)))
        assert len(sources) == 1
        source = sources[0]
        assert (
            source.task_id,
            source.user_id,
            source.workspace_id,
            source.source_id,
            source.connector_id,
        ) == (task_id, env.user_id, env.workspace_id, env.source_id, env.connector_id)
        saved = await database.get(StudentEmailReview, review_id)
        assert saved.accepted_task_id == task_id
        assert saved.acceptance_hash == service.digest(payload.model_dump(mode="json"))
        # Replay remains truthful after a task deletion, and never recreates it.
        await database.delete(task)
        await database.commit()
        replay = await service.accept_task(
            database,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            review_id=review_id,
            payload=payload,
        )
        assert replay["reused"] and not replay["available"]
        assert await database.scalar(select(func.count()).select_from(EmailTaskSource)) == 0
        assert await database.scalar(select(func.count()).select_from(StudentTask)) == 0
    assert env.reader.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["source_forget", "student_reset"])
async def test_postgres_erasure_wins_after_slow_analysis_waits_for_fresh_owner_lock(
    postgres_email_env, monkeypatch, operation
):
    env = postgres_email_env
    old_review, _ = await corrected(env, await analyzed(env))
    await accept(env, old_review)  # Erasure must remove an actual existing derived task too.
    reader_entered, finish_read, owner_wait_started = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    original_owner = schedule.owner
    request = AnalyzeInput(request_id=uuid4(), source_id=env.source_id, source_hash=env.source_hash)

    async def slow_read():
        reader_entered.set()
        await finish_read.wait()

    env.reader.callback = slow_read
    # Pin a physical connection: analyze commits its reservation before fake I/O,
    # so a normal pooled Session could otherwise resume on a different backend PID.
    async with (
        env.factory.kw["bind"].connect() as analyzer_connection,
        env.factory(bind=analyzer_connection) as analyzer,
        env.factory() as eraser,
        env.factory() as observer,
    ):
        analyzer_pid = await analyzer.scalar(text("SELECT pg_backend_pid()"))
        eraser_pid = await eraser.scalar(text("SELECT pg_backend_pid()"))

        async def observe_owner(database, *args, **kwargs):
            if database is analyzer and kwargs.get("lock") and finish_read.is_set():
                owner_wait_started.set()
            return await original_owner(database, *args, **kwargs)

        monkeypatch.setattr(schedule, "owner", observe_owner)
        work = asyncio.create_task(
            service.analyze(
                analyzer,
                env.settings,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                payload=request,
                reader=env.reader,
            )
        )
        try:
            await asyncio.wait_for(reader_entered.wait(), 10)
            # The analyzer committed its empty reservation and released User before fake I/O.
            assert not analyzer.in_transaction()
            await original_owner(eraser, env.user_id, env.workspace_id, lock=True)
            pending = await eraser.scalar(
                select(StudentEmailReview).where(
                    StudentEmailReview.request_id == request.request_id
                )
            )
            assert pending.revision == 0 and pending.encrypted_payload is None
            finish_read.set()
            await asyncio.wait_for(owner_wait_started.wait(), 10)
            await wait_for_real_lock_waits(observer, [analyzer_pid], eraser_pid)
            assert not work.done()
            if operation == "source_forget":
                await service.forget_email_data(
                    eraser,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    source_id=env.source_id,
                )
                await eraser.commit()
            else:
                await schedule.reset_data(
                    eraser,
                    env.user_id,
                    env.workspace_id,
                    revision=0,
                    request_id=uuid4(),
                    settings=env.settings,
                )
            with pytest.raises(service.EmailError) as denied:
                await asyncio.wait_for(work, 15)
            assert denied.value.status_code == 410
            await analyzer.rollback()
        finally:
            finish_read.set()
            await eraser.rollback()
            if not work.done():
                work.cancel()
            await asyncio.gather(work, return_exceptions=True)
    async with env.factory() as database:
        reviews = list(
            await database.scalars(
                select(StudentEmailReview).where(
                    StudentEmailReview.user_id == env.user_id,
                    StudentEmailReview.workspace_id == env.workspace_id,
                )
            )
        )
        assert len(reviews) == 2
        assert all(
            row.encrypted_payload is None
            and row.source_id is None
            and row.connector_id is None
            and row.source_hash is None
            and row.authority_hash is None
            for row in reviews
        )
        for model in (StudentTask, EmailTaskSource, StudentStudyBlock):
            assert await database.scalar(select(func.count()).select_from(model)) == 0
        with pytest.raises(service.EmailError) as replay:
            await service.analyze(
                database,
                env.settings,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                payload=request,
                reader=env.reader,
            )
        assert replay.value.status_code == 410
        # Both writes share the same fresh owner; the existing personal owner survives reset.
        assert await database.get(User, env.user_id) is not None
    assert env.reader.calls == 2
