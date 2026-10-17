"""Bounded local formatting with fresh owner/course/reset checks before publication."""

import asyncio
import contextlib
import json
import os
import signal
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.coursework import CourseWorkspaceState
from navox.db.student import StudentCourse
from navox.db.student_goals import StudentGoalState
from navox.student import service as schedule
from navox.student_work_export.schemas import MAX_PDF_BYTES, ExportContext, ExportInput

SLOTS = asyncio.Semaphore(2)
WORKER_TIMEOUT_SECONDS = 10
_BOOTSTRAP = """import os,resource,runpy,sys
resource.setrlimit(resource.RLIMIT_CPU,(5,5))
resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
def guard(event,args):
 if event.startswith('socket.') or event in (
  'subprocess.Popen','os.system','os.exec','os.posix_spawn'):
  raise PermissionError('Export worker transport disabled')
 if (event=='open' and len(args)>2 and isinstance(args[2],int)
  and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
  raise PermissionError('Export worker disk writes disabled')
 if event in ('os.remove','os.rename','os.mkdir','os.rmdir','os.symlink','os.link','os.truncate'):
  raise PermissionError('Export worker filesystem mutations disabled')
sys.addaudithook(guard)
sys.path.insert(0,sys.argv[2])
runpy.run_path(sys.argv[1],run_name='__main__')
"""


class ExportError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Snapshot:
    course_revision: int
    generation: int
    course_label: str
    workspace_state: tuple[UUID, int] | None


async def snapshot(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, course_id: UUID
) -> Snapshot:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    course = await database.scalar(
        select(StudentCourse)
        .where(
            StudentCourse.id == course_id,
            StudentCourse.user_id == user_id,
            StudentCourse.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if course is None:
        raise ExportError("The selected course is unavailable", 404)
    if course.archived:
        raise ExportError("Restore this course before formatting your work", 409)
    generation = await database.scalar(
        select(StudentGoalState.generation).where(
            StudentGoalState.user_id == user_id, StudentGoalState.workspace_id == workspace_id
        )
    )
    # Metadata only: no coursework ciphertext, files, linked email or history.
    row = (
        await database.execute(
            select(CourseWorkspaceState.id, CourseWorkspaceState.revision).where(
                CourseWorkspaceState.user_id == user_id,
                CourseWorkspaceState.workspace_id == workspace_id,
                CourseWorkspaceState.course_id == course_id,
            )
        )
    ).first()
    label = " - ".join(value for value in (course.code, course.name) if value)
    return Snapshot(course.revision, generation or 0, label, (row[0], row[1]) if row else None)


async def context(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, course_id: UUID
) -> ExportContext:
    current = await snapshot(database, user_id, workspace_id, course_id)
    return ExportContext(
        course_revision=current.course_revision,
        generation=current.generation,
        course_label=current.course_label,
    )


@asynccontextmanager
async def export_slot() -> AsyncIterator[None]:
    if SLOTS.locked():
        raise ExportError("Two documents are already being formatted. Try again shortly", 429)
    await SLOTS.acquire()
    try:
        yield
    finally:
        SLOTS.release()


async def run_worker(payload: ExportInput) -> bytes:
    worker = Path(__file__).with_name("worker.py").resolve()
    api_root = worker.parents[2]
    launch = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            "-c",
            _BOOTSTRAP,
            str(worker),
            str(api_root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "RL_trustedHosts": "",
                "RL_trustedSchemes": "",
            },
        )
    )
    try:
        process = await asyncio.shield(launch)
    except asyncio.CancelledError:
        # Resolve a pending spawn before returning cancellation; a child created
        # at that boundary must not outlive its request.
        with contextlib.suppress(OSError):
            process = await launch
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        raise
    except OSError:
        raise ExportError("Local PDF formatting is unavailable", 503) from None
    assert process.stdin is not None and process.stdout is not None

    async def feed() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(payload.model_dump_json().encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    writer = asyncio.create_task(feed())
    output = bytearray()
    try:
        async with asyncio.timeout(WORKER_TIMEOUT_SECONDS):
            while chunk := await process.stdout.read(8192):
                if len(output) + len(chunk) > MAX_PDF_BYTES:
                    raise ExportError("This document exceeds the 2 MiB export limit", 413)
                output.extend(chunk)
            await writer
            if await process.wait() != 0:
                raise ExportError("Local PDF formatting is unavailable", 503)
        if output.startswith(b"%PDF-"):
            return bytes(output)
        try:
            code = json.loads(output).get("error")
        except (ValueError, AttributeError, UnicodeError):
            code = None
        errors = {
            "unsupported_text": (
                "Some characters or writing systems are not supported by this export font",
                422,
            ),
            "page_limit": ("This text needs more than 20 pages including its title page", 422),
            "metadata_limit": (
                "The title or metadata needs too much page space. Use shorter lines",
                422,
            ),
            "size_limit": ("This document exceeds the 2 MiB export limit", 413),
            "input_limit": ("This export exceeds the request size limit", 413),
        }
        message, status = errors.get(
            code if isinstance(code, str) else "", ("Local PDF formatting is unavailable", 503)
        )
        raise ExportError(message, status)
    except TimeoutError:
        raise ExportError("Formatting took too long. Try a shorter document", 408) from None
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        await process.wait()


async def export(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    course_id: UUID,
    payload: ExportInput,
    reauthorize: Callable[[], Awaitable[tuple[UUID, UUID]]],
) -> bytes:
    async with export_slot():
        before = await snapshot(database, user_id, workspace_id, course_id)
        if (
            before.course_revision != payload.course_revision
            or before.generation != payload.generation
        ):
            raise ExportError(
                "Your course or student data changed. Review a fresh export context", 409
            )
        if payload.course_label is not None and payload.course_label != before.course_label:
            raise ExportError("Review the current saved course label before including it", 409)
        # Release the owner lock while the isolated process runs, so erasure can
        # take effect. Recheck the durable generation and all metadata afterwards.
        await database.rollback()
        output = await run_worker(payload)
        if not getattr(settings, "student_work_export_enabled", False):
            raise ExportError("Student work export is not enabled", 404)
        after = await snapshot(database, user_id, workspace_id, course_id)
        if after != before:
            raise ExportError(
                "Your course or student data changed while formatting. Review a fresh export", 409
            )
        if await reauthorize() != (user_id, workspace_id):
            raise ExportError("Your account changed. Review a fresh export context", 409)
        # The final User lock stays held until the response dependency closes,
        # ordering publication before a subsequent owner erasure transaction.
        return output
