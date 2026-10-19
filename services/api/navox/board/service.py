"""Explicit saved authored Boards; no model, provider or background operations."""

import hashlib
import hmac
import json
from typing import Any, cast
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.board.schemas import (
    BoardCreate,
    BoardErase,
    BoardSnapshot,
    BoardSummary,
    BoardsView,
    BoardUpdate,
    BoardView,
)
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.import_storage import cipher
from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.board import SavedBoard, SavedBoardCommand, SavedBoardState
from navox.db.student import StudentCourse
from navox.student import service as schedule

MAX_BOARDS = 50
MAX_COMMANDS = 5000
# At the ordinary command limit, every remaining board can still be erased.
MAX_FENCES = MAX_COMMANDS + MAX_BOARDS
MAX_ENCRYPTED_PAYLOAD_BYTES = 4 * 1024 * 1024


def fail(message: str, status: int = 409) -> schedule.StudentError:
    return schedule.StudentError(status, message)


def owned(model: Any, user_id: UUID, workspace_id: UUID) -> tuple[Any, Any]:
    return model.user_id == user_id, model.workspace_id == workspace_id


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def binding(row: SavedBoard) -> dict[str, object]:
    return {
        "version": 1,
        "purpose": "student_saved_board",
        "id": str(row.id),
        "user_id": str(row.user_id),
        "workspace_id": str(row.workspace_id),
        "course_id": str(row.course_id) if row.course_id is not None else None,
        "revision": row.revision,
    }


def seal(row: SavedBoard, snapshot: BoardSnapshot, settings: Settings) -> str:
    try:
        token = cipher(settings).encrypt(
            canonical({**binding(row), "payload": snapshot.model_dump(mode="json")})
        )
        if len(token) > MAX_ENCRYPTED_PAYLOAD_BYTES:
            raise fail("The saved Board exceeds its storage limit", 413)
        return token.decode()
    except ConnectorRuntimeError:
        raise fail("Private Board encryption is not configured", 503) from None


def stored_payload(row: SavedBoard, settings: Settings) -> dict[str, Any]:
    try:
        if len(row.encrypted_payload) > MAX_ENCRYPTED_PAYLOAD_BYTES:
            raise ValueError
        envelope = json.loads(cipher(settings).decrypt(row.encrypted_payload.encode()))
        if not isinstance(envelope, dict) or {
            key: value for key, value in envelope.items() if key != "payload"
        } != binding(row):
            raise ValueError
        if not isinstance(envelope["payload"], dict):
            raise ValueError
        return cast(dict[str, Any], envelope["payload"])
    except (InvalidToken, ConnectorRuntimeError, ValueError, KeyError, TypeError, UnicodeError):
        raise fail("This private Board cannot be decrypted", 503) from None


def reveal(row: SavedBoard, settings: Settings) -> BoardSnapshot:
    try:
        return BoardSnapshot.model_validate(stored_payload(row, settings))
    except (ValueError, TypeError):
        raise fail(
            "This saved Board edition is unavailable. Its saved entry can be erased", 503
        ) from None


async def state(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> SavedBoardState:
    row = await database.scalar(
        select(SavedBoardState)
        .where(*owned(SavedBoardState, user_id, workspace_id))
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = SavedBoardState(
            id=uuid4(), user_id=user_id, workspace_id=workspace_id, revision=0, generation=0
        )
        database.add(row)
        await database.flush()
    return row


async def course(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID | None,
    *,
    mutation: bool = False,
) -> None:
    if course_id is None:
        return
    row = await schedule.get_owned(database, StudentCourse, course_id, user_id, workspace_id)
    if mutation and row.archived:
        raise fail("Restore this archived course before changing its saved Boards")


async def board(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, board_id: UUID
) -> SavedBoard:
    row = await database.scalar(
        select(SavedBoard)
        .where(SavedBoard.id == board_id, *owned(SavedBoard, user_id, workspace_id))
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise fail("Board was not found", 404)
    await course(database, user_id, workspace_id, row.course_id)
    return row


def summary(row: SavedBoard, payload: dict[str, Any]) -> BoardSummary:
    # The list stays usable for erasure when a formerly valid authored edition
    # is no longer in the current renderer catalog. Full opening is stricter.
    try:
        return BoardSummary(
            id=row.id,
            revision=row.revision,
            course_id=row.course_id,
            created_at=utc(row.created_at),
            updated_at=utc(row.updated_at),
            title=payload["title"],
            lesson_id=payload["lesson_id"],
            lesson_version=payload["lesson_version"],
            step_index=payload["step_index"],
            complete=payload["complete"],
        )
    except (ValueError, KeyError, TypeError):
        raise fail("This private Board cannot be read", 503) from None


def view(row: SavedBoard, settings: Settings) -> BoardView:
    snapshot = reveal(row, settings)
    return BoardView(**summary(row, snapshot.model_dump()).model_dump(), snapshot=snapshot)


def fingerprint(settings: Settings | None, value: object) -> str:
    # Delete requests contain only opaque IDs and revisions, allowing key-free erasure.
    if settings is None:
        return hashlib.sha256(canonical(value)).hexdigest()
    try:
        cipher(settings)
        secret = next(
            key.get_secret_value()
            for key in (
                settings.connector_secret_encryption_key,
                settings.google_token_encryption_key,
            )
            if key is not None and key.get_secret_value()
        )
    except (ConnectorRuntimeError, StopIteration):
        raise fail("Private Board encryption is not configured", 503) from None
    key = hmac.digest(secret.encode(), b"navox.student-board.retry.v1", "sha256")
    return hmac.new(key, canonical(value), hashlib.sha256).hexdigest()


async def command(
    database: AsyncSession,
    settings: Settings | None,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID,
    operation: str,
    board_id: UUID | None,
    payload: dict[str, Any],
) -> tuple[SavedBoardCommand | None, str]:
    previous = await database.scalar(
        select(SavedBoardCommand)
        .where(
            *owned(SavedBoardCommand, user_id, workspace_id),
            SavedBoardCommand.request_id == request_id,
        )
        .execution_options(populate_existing=True)
    )
    if previous is not None and previous.status == "erased":
        raise fail("This request belongs to erased Board data. Review a fresh Board", 410)
    digest = fingerprint(
        settings,
        {
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "operation": operation,
            "board_id": str(board_id) if board_id is not None else None,
            "payload": payload,
        },
    )
    if previous is not None:
        if previous.operation != operation or not hmac.compare_digest(
            previous.payload_hash, digest
        ):
            raise fail("This request ID was already used with different Board details")
        return previous, digest
    count = await database.scalar(
        select(func.count())
        .select_from(SavedBoardCommand)
        .where(*owned(SavedBoardCommand, user_id, workspace_id))
    )
    maximum = MAX_FENCES if operation == "delete" else MAX_COMMANDS
    if (count or 0) >= maximum:
        raise fail("The saved Board request limit was reached. Erasure remains available")
    return None, digest


def remember(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID,
    operation: str,
    digest: str,
    row: SavedBoard,
) -> None:
    database.add(
        SavedBoardCommand(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=request_id,
            operation=operation,
            payload_hash=digest,
            board_id=row.id,
            course_id=row.course_id,
            applied_revision=row.revision,
            status="applied",
        )
    )


async def replay(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    previous: SavedBoardCommand,
) -> BoardView:
    try:
        row = await board(database, user_id, workspace_id, previous.board_id)
    except schedule.StudentError as error:
        if error.status_code == 404:
            raise fail(
                "This request belongs to erased Board data. Review a fresh Board", 410
            ) from None
        raise
    if row.revision != previous.applied_revision:
        raise fail("This request already succeeded and the Board has since changed. Refresh")
    return view(row, settings)


async def collection(
    database: AsyncSession, settings: Settings, user_id: UUID, workspace_id: UUID
) -> BoardsView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    rows = list(
        await database.scalars(
            select(SavedBoard)
            .where(*owned(SavedBoard, user_id, workspace_id))
            .order_by(SavedBoard.updated_at.desc(), SavedBoard.id.desc())
            .limit(MAX_BOARDS + 1)
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) > MAX_BOARDS:
        raise fail("Review the saved Board limit before continuing")
    summaries = []
    for row in rows:
        await course(database, user_id, workspace_id, row.course_id)
        summaries.append(summary(row, stored_payload(row, settings)))
    owner_state = await database.scalar(
        select(SavedBoardState)
        .where(*owned(SavedBoardState, user_id, workspace_id))
        .execution_options(populate_existing=True)
    )
    return BoardsView(
        revision=owner_state.revision if owner_state is not None else 0,
        generation=owner_state.generation if owner_state is not None else 0,
        boards=summaries,
    )


async def detail(
    database: AsyncSession, settings: Settings, user_id: UUID, workspace_id: UUID, board_id: UUID
) -> BoardView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    return view(await board(database, user_id, workspace_id, board_id), settings)


async def create(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    payload: BoardCreate,
) -> BoardView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    previous, digest = await command(
        database,
        settings,
        user_id,
        workspace_id,
        payload.request_id,
        "create",
        None,
        payload.model_dump(mode="json"),
    )
    if previous is not None:
        return await replay(database, settings, user_id, workspace_id, previous)
    owner_state = await state(database, user_id, workspace_id)
    if payload.generation != owner_state.generation:
        raise fail("Your Boards were reset. Review a fresh saved Board view before saving")
    await course(database, user_id, workspace_id, payload.course_id, mutation=True)
    await schedule.count_limit(database, SavedBoard, user_id, workspace_id, MAX_BOARDS)
    now = utc(await database_now(database))
    row = SavedBoard(
        id=uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        course_id=payload.course_id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    row.encrypted_payload = seal(row, payload.snapshot, settings)
    database.add(row)
    owner_state.revision += 1
    remember(database, user_id, workspace_id, payload.request_id, "create", digest, row)
    await database.flush()
    response = view(row, settings)
    await database.commit()
    return response


async def change(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    board_id: UUID,
    payload: BoardUpdate,
) -> BoardView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    previous, digest = await command(
        database,
        settings,
        user_id,
        workspace_id,
        payload.request_id,
        "update",
        board_id,
        payload.model_dump(mode="json"),
    )
    if previous is not None:
        return await replay(database, settings, user_id, workspace_id, previous)
    row = await board(database, user_id, workspace_id, board_id)
    await course(database, user_id, workspace_id, row.course_id, mutation=True)
    if row.revision != payload.revision:
        raise fail("This Board changed. Refresh before saving")
    row.revision += 1
    row.updated_at = utc(await database_now(database))
    row.encrypted_payload = seal(row, payload.snapshot, settings)
    owner_state = await state(database, user_id, workspace_id)
    owner_state.revision += 1
    remember(database, user_id, workspace_id, payload.request_id, "update", digest, row)
    await database.flush()
    response = view(row, settings)
    await database.commit()
    return response


async def invalidate(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    board_id: UUID | None = None,
    course_id: UUID | None = None,
) -> None:
    query = update(SavedBoardCommand).where(
        *owned(SavedBoardCommand, user_id, workspace_id), SavedBoardCommand.operation != "delete"
    )
    if board_id is not None:
        query = query.where(SavedBoardCommand.board_id == board_id)
    if course_id is not None:
        query = query.where(SavedBoardCommand.course_id == course_id)
    await database.execute(query.values(status="erased"))


async def erase(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    board_id: UUID,
    payload: BoardErase,
) -> None:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    previous, digest = await command(
        database,
        None,
        user_id,
        workspace_id,
        payload.request_id,
        "delete",
        board_id,
        payload.model_dump(mode="json"),
    )
    if previous is not None:
        return
    row = await board(database, user_id, workspace_id, board_id)
    if row.revision != payload.revision:
        raise fail("This Board changed. Refresh before erasing")
    await invalidate(database, user_id, workspace_id, board_id=board_id)
    remember(database, user_id, workspace_id, payload.request_id, "delete", digest, row)
    await database.delete(row)
    owner_state = await state(database, user_id, workspace_id)
    owner_state.revision += 1
    owner_state.generation += 1
    await database.commit()


async def forget_board_data(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID | None = None,
) -> None:
    """Caller holds the shared User lock and transaction. Never decrypt or commit.

    Retry fences survive erasure; every erasure invalidates unsent old creates.
    A course FK cascade removes payloads, and replay independently checks existence.
    """
    await invalidate(database, user_id, workspace_id, course_id=course_id)
    query = delete(SavedBoard).where(*owned(SavedBoard, user_id, workspace_id))
    if course_id is not None:
        query = query.where(SavedBoard.course_id == course_id)
    await database.execute(query)
    owner_state = await state(database, user_id, workspace_id)
    owner_state.revision += 1
    owner_state.generation += 1
