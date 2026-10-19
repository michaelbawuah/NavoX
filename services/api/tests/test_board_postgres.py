"""Proven PostgreSQL User row-lock interleavings for private saved Boards.

NAVOX_CONNECTOR_TEST_DSN opts into the existing isolated-schema fixture.
SQLite explicitly skips: a queued coroutine is not proof of a database lock.
"""

import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from test_board import board_env, create, create_body, snapshot  # noqa: F401

from navox.board import service
from navox.board.schemas import BoardCreate, BoardErase, BoardUpdate
from navox.db.board import SavedBoard, SavedBoardCommand, SavedBoardState
from navox.student import service as schedule


@pytest_asyncio.fixture
async def postgres_board_env(board_env):  # noqa: F811 - imported shared pytest fixture
    if board_env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL User row locks and pg_blocking_pids")
    return board_env


async def wait_for_lock(observer, pids: list[int], holder_pid: int) -> None:
    """Wait for SQL-observed blocking, rather than assuming task scheduling order."""
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
@pytest.mark.parametrize("same_request", [True, False])
async def test_postgres_waiting_writers_refresh_stale_state_and_apply_only_one_revision(
    postgres_board_env, same_request
):
    env = postgres_board_env
    row, _ = await create(env)
    board_id = UUID(row["id"])
    first_input = BoardUpdate(
        request_id=uuid4(), revision=1, snapshot=snapshot(title="The committed private state")
    )
    second_input = (
        first_input if same_request else first_input.model_copy(update={"request_id": uuid4()})
    )
    async with (
        env.factory() as holder,
        env.factory() as first,
        env.factory() as second,
        env.factory() as observer,
    ):
        stale_first = await first.get(SavedBoard, board_id)
        stale_second = await second.get(SavedBoard, board_id)
        assert stale_first.revision == stale_second.revision == 1
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        await schedule.owner(holder, env.user_id, env.workspace_id, lock=True)

        async def write(database, payload):
            try:
                return await service.change(
                    database, env.settings, env.user_id, env.workspace_id, board_id, payload
                )
            except schedule.StudentError as error:
                await database.rollback()
                return error

        writes = [
            asyncio.create_task(write(first, first_input)),
            asyncio.create_task(write(second, second_input)),
        ]
        try:
            await wait_for_lock(observer, [first_pid, second_pid], holder_pid)
            assert not any(task.done() for task in writes)
            await holder.commit()
            outcomes = await asyncio.wait_for(asyncio.gather(*writes), 15)
        finally:
            await holder.rollback()
            for task in writes:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*writes, return_exceptions=True)
    if same_request:
        assert outcomes[0].model_dump() == outcomes[1].model_dump()
        assert outcomes[0].revision == 2
    else:
        successes = [
            outcome for outcome in outcomes if not isinstance(outcome, schedule.StudentError)
        ]
        errors = [outcome for outcome in outcomes if isinstance(outcome, schedule.StudentError)]
        assert len(successes) == len(errors) == 1 and errors[0].status_code == 409
        assert successes[0].revision == 2
    async with env.factory() as database:
        stored = await database.get(SavedBoard, board_id)
        assert stored.revision == 2
        state = await database.scalar(select(SavedBoardState))
        assert state.revision == 2 and state.generation == 0
        assert await database.scalar(select(func.count()).select_from(SavedBoardCommand)) == 2
        assert service.reveal(stored, env.settings).title == "The committed private state"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "reset"])
async def test_postgres_erasure_precedes_waiting_stale_write_without_resurrection(
    postgres_board_env, operation
):
    env = postgres_board_env
    row, _ = await create(env)
    board_id = UUID(row["id"])
    async with (
        env.factory() as eraser,
        env.factory() as writer,
        env.factory() as observer,
    ):
        # Keep strong references: the resumed mutation must refresh these identities.
        stale_board = await writer.get(SavedBoard, board_id)
        stale_state = await writer.scalar(select(SavedBoardState))
        assert stale_board.revision == 1 and stale_state.generation == 0
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        eraser_pid = await eraser.scalar(text("SELECT pg_backend_pid()"))
        await schedule.owner(eraser, env.user_id, env.workspace_id, lock=True)
        if operation == "delete":
            work = asyncio.create_task(
                service.change(
                    writer,
                    env.settings,
                    env.user_id,
                    env.workspace_id,
                    board_id,
                    BoardUpdate(request_id=uuid4(), revision=1, snapshot=snapshot()),
                )
            )
        else:
            work = asyncio.create_task(
                service.create(
                    writer,
                    env.settings,
                    env.user_id,
                    env.workspace_id,
                    BoardCreate.model_validate(create_body()),
                )
            )
        try:
            await wait_for_lock(observer, [writer_pid], eraser_pid)
            assert not work.done()
            if operation == "delete":
                await service.erase(
                    eraser,
                    env.user_id,
                    env.workspace_id,
                    board_id,
                    BoardErase(request_id=uuid4(), revision=1),
                )
            else:
                await schedule.reset_data(
                    eraser,
                    env.user_id,
                    env.workspace_id,
                    revision=0,
                    request_id=uuid4(),
                    settings=env.settings,
                )
            with pytest.raises(schedule.StudentError) as rejected:
                await asyncio.wait_for(work, 15)
            assert rejected.value.status_code == (404 if operation == "delete" else 409)
        finally:
            await eraser.rollback()
            await writer.rollback()
            if not work.done():
                work.cancel()
            await asyncio.gather(work, return_exceptions=True)
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(SavedBoard)) == 0
        state = await database.scalar(select(SavedBoardState))
        assert state.revision == 2 and state.generation == 1
        saved_commands = list(await database.scalars(select(SavedBoardCommand)))
        assert all(
            command.status == "erased"
            for command in saved_commands
            if command.operation != "delete"
        )
