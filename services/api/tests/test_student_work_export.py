"""Own text stays literal and ephemeral; stale or erased authority cannot publish."""

import asyncio
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select, text, update

from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.communications import AssistantTurn
from navox.db.coursework import CourseWorkspaceState
from navox.db.models import AuditEvent, User, UserSession
from navox.db.session import get_database_session
from navox.db.student import StudentCourse
from navox.db.student_goals import StudentGoalState
from navox.student import service as schedule
from navox.student.schemas import CourseUpdate
from navox.student_work_export import render, service
from navox.student_work_export.schemas import MAX_PDF_BYTES, ExportInput

PRIVATE_TEXT = "My private original work."


def body(**changes):
    return {
        "course_revision": 1,
        "generation": 0,
        "title": "My own assignment",
        "text": PRIVATE_TEXT,
        "confirmed_own_work": True,
        "confirmed_metadata": True,
        **changes,
    }


def extracted(tmp_path, value, *, layout=False):
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(value)
    return subprocess.run(
        ["pdftotext", *(["-layout"] if layout else []), str(source), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest_asyncio.fixture
async def export_env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider="disabled",
        student_work_export_enabled=True,
    )

    async def database():
        async with ai_database() as session:
            yield session

    from navox.ai import configured

    def forbidden(*_args, **_kwargs):
        pytest.fail("Own-work export must never construct a model runtime")

    monkeypatch.setattr(configured, "build_runtime", forbidden)
    app = create_app()
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": settings.web_origin},
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "own-work@example.com",
                "password": "twelve-character-password",
                "display_name": "Never infer this as the author",
            },
        )
        assert response.status_code == 201, response.text
        account = response.json()
        user_id, workspace_id = UUID(account["id"]), UUID(account["workspace"]["id"])
        async with ai_database() as database:
            user = await database.get(User, user_id)
            user.agent_paused = True
            await database.commit()
        course = await client.post(
            "/api/v1/student/courses",
            json={
                "code": "ENG101",
                "name": "Writing",
                "term_start": "2026-09-01",
                "term_end": "2026-12-31",
            },
        )
        assert course.status_code == 201, course.text
        course_id = UUID(course.json()["id"])
        yield SimpleNamespace(
            client=client,
            factory=ai_database,
            app=app,
            settings=settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=course_id,
            url=f"/api/v1/student/courses/{course_id}/work-export",
        )


def test_strict_confirmation_text_and_limits():
    for changes in (
        {"confirmed_own_work": 1},
        {"confirmed_metadata": "true"},
        {"text": "x" * 20001},
        {"text": " \n"},
        {"text": "a\tb"},
        {"title": "two\nlines"},
        {"course_revision": True},
        {"generation": -1},
        {"source_id": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            ExportInput(**body(**changes))
    assert ExportInput(**body(text="  Text\r\n\nlast line  ")).text == "  Text\r\n\nlast line  "


def test_exact_literal_content_order_metadata_and_determinism(tmp_path):
    text = (
        "Café, Ελληνικά, русский: π ≥ 3.\n\n"
        "A literal <script> and https://example.com\nLast paragraph."
    )
    payload = ExportInput(
        **body(
            title="Original work",
            author="My chosen attribution",
            course_label="ENG101 - Writing",
            text=text,
        )
    )
    value = render.render(payload)
    assert value == render.render(payload)
    pages = extracted(tmp_path, value).split("\f")
    assert [line for line in pages[0].splitlines() if line] == [
        "Original work",
        "My chosen attribution",
        "ENG101 - Writing",
        "Page 1 of 2",
    ]
    assert [line for line in pages[1].splitlines() if line] == [
        "Original work",
        *[line for line in text.splitlines() if line],
        "Page 2 of 2",
    ]
    assert (
        b"/Annots" not in value and b"/JavaScript" not in value and b"/EmbeddedFiles" not in value
    )
    assert b"Never infer" not in value


def test_wrap_preserves_all_characters_and_bounded_multipage_layout(tmp_path):
    render.fonts()
    for line in ("W" * 2000, "  repeated    spaces  " * 60, "αβγ " * 500):
        lines = render.wrap_line(line, render.REGULAR, render.BODY_SIZE)
        assert "".join(lines) == line
        assert all(
            render.pdfmetrics.stringWidth(item, render.REGULAR, render.BODY_SIZE)
            <= render.CONTENT_WIDTH
            for item in lines
        )
    text = "\n".join(f"Line {index:03d}: My own recorded text." for index in range(110))
    payload = ExportInput(**body(text=text))
    document = render.layout(payload)
    assert len(document.pages) == 3
    assert [line for page in document.pages for line in page] == text.splitlines()
    pages = extracted(tmp_path, render.render(payload)).split("\f")
    actual = []
    for page in pages[1:4]:
        actual.extend([line for line in page.splitlines() if line][1:-1])
    assert actual == text.splitlines()
    for index, page in enumerate(pages[:4], start=1):
        assert f"Page {index} of 4" in page


@pytest.mark.parametrize("text", ["漢字", "مرحبا", "e\u0301", "🧠"])
def test_unsupported_glyph_or_unverified_shaping_fails_honestly(text):
    with pytest.raises(render.RenderError, match="^unsupported_text$"):
        render.render(ExportInput(**body(text=text)))


def test_page_output_and_font_integrity_limits(monkeypatch):
    with pytest.raises(render.RenderError, match="^page_limit$"):
        render.render(ExportInput(**body(text="line\n" * 1000)))
    target = render.LimitedBuffer()
    target.write(b"x" * MAX_PDF_BYTES)
    with pytest.raises(render.RenderError, match="^size_limit$"):
        target.write(b"x")
    monkeypatch.setitem(render.FONT_FILES, "DejaVuSans.ttf", "0" * 64)
    with pytest.raises(render.RenderError, match="^renderer_unavailable$"):
        render.render(ExportInput(**body()))


def test_twenty_page_boundary_includes_cover_without_truncating(tmp_path):
    payload = ExportInput(**body(text="\n".join(f"Own line {index:03d}" for index in range(817))))
    document = render.layout(payload)
    assert len(document.pages) == 19
    assert document.pages[-1][-1] == "Own line 816"
    pages = extracted(tmp_path, render.render(payload)).split("\f")
    assert len(pages) == 21 and "Page 20 of 20" in pages[19]
    with pytest.raises(render.RenderError, match="^page_limit$"):
        render.render(payload.model_copy(update={"text": payload.text + "\nOne line too many"}))


def test_bundled_font_license_and_pinned_asset_manifest():
    directory = Path(render.__file__).with_name("fonts")
    manifest = json.loads((directory / "PROVENANCE.json").read_text())
    assert manifest["version"] == "2.37"
    for filename, expected in manifest["files"].items():
        assert hashlib.sha256((directory / filename).read_bytes()).hexdigest() == expected
    license_text = (directory / "LICENSE.txt").read_text()
    assert "Permission is hereby granted, free of charge" in license_text
    assert "Bitstream" in license_text and "Arev" in license_text


@pytest.mark.asyncio
async def test_real_isolated_worker_matches_local_renderer():
    payload = ExportInput(**body(text="Café and π.\nOwn work, unchanged."))
    assert await service.run_worker(payload) == render.render(payload)


@pytest.mark.asyncio
async def test_fresh_worker_denies_transport_writes_children_and_credentials(monkeypatch, tmp_path):
    probe = tmp_path / "worker-must-not-create.txt"
    bootstrap = service._BOOTSTRAP.rsplit("\nrunpy.run_path", 1)[0]
    bootstrap += f"""
import socket,subprocess
blocked=0
for operation in (
 lambda:open({str(probe)!r},'wb'),
 lambda:socket.socket(),
 lambda:subprocess.Popen(['/bin/true'])):
 try:operation()
 except PermissionError:blocked+=1
assert blocked==3 and 'NAVOX_EXPORT_TEST_SECRET' not in os.environ
assert resource.getrlimit(resource.RLIMIT_CPU)==(5,5)
assert resource.getrlimit(resource.RLIMIT_AS)==(268435456,268435456)
assert resource.getrlimit(resource.RLIMIT_FSIZE)==(0,0)
sys.stdout.buffer.write(b'%PDF-capabilities-blocked')
"""
    monkeypatch.setenv("NAVOX_EXPORT_TEST_SECRET", "not-inherited")
    monkeypatch.setattr(service, "_BOOTSTRAP", bootstrap)
    assert await service.run_worker(ExportInput(**body())) == b"%PDF-capabilities-blocked"
    assert not probe.exists()


@pytest.mark.asyncio
async def test_cancel_during_launch_resolves_and_reaps_child(monkeypatch):
    launched, release = asyncio.Event(), asyncio.Event()
    real_spawn = asyncio.create_subprocess_exec
    spawned = []

    async def delayed(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        spawned.append(process)
        launched.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    monkeypatch.setattr(service, "_BOOTSTRAP", "import time;time.sleep(20)")
    task = asyncio.create_task(service.run_worker(ExportInput(**body())))
    await asyncio.wait_for(launched.wait(), 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert spawned[0].returncode is not None


@pytest.mark.asyncio
async def test_spawn_failure_is_content_free_and_bounded(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise OSError(PRIVATE_TEXT)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unavailable)
    with pytest.raises(service.ExportError) as failure:
        await service.run_worker(ExportInput(**body()))
    assert failure.value.status_code == 503 and PRIVATE_TEXT not in str(failure.value)


@pytest.mark.asyncio
async def test_worker_timeout_output_bound_error_and_cancellation(monkeypatch):
    spawned = []
    real_spawn = asyncio.create_subprocess_exec

    async def observe(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    monkeypatch.setattr(service, "WORKER_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(service, "_BOOTSTRAP", "import time;time.sleep(20)")
    with pytest.raises(service.ExportError) as failure:
        await service.run_worker(ExportInput(**body()))
    assert failure.value.status_code == 408 and spawned[-1].returncode is not None
    monkeypatch.setattr(service, "WORKER_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(service, "_BOOTSTRAP", "import sys;sys.stdout.buffer.write(b'x'*2097153)")
    with pytest.raises(service.ExportError) as failure:
        await service.run_worker(ExportInput(**body()))
    assert failure.value.status_code == 413 and spawned[-1].returncode is not None
    monkeypatch.setattr(service, "_BOOTSTRAP", "print('{\"error\":[]}')")
    with pytest.raises(service.ExportError) as failure:
        await service.run_worker(ExportInput(**body()))
    assert failure.value.status_code == 503
    monkeypatch.setattr(service, "_BOOTSTRAP", "import time;time.sleep(20)")
    task = asyncio.create_task(service.run_worker(ExportInput(**body())))
    while len(spawned) < 4:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert spawned[-1].returncode is not None


@pytest.mark.asyncio
async def test_context_optional_metadata_and_private_download_without_writes(export_env, tmp_path):
    env = export_env
    context = await env.client.get(env.url + "/context")
    assert context.status_code == 200
    assert context.json() == {
        "course_revision": 1,
        "generation": 0,
        "course_label": "ENG101 - Writing",
        "max_text_chars": 20000,
        "max_pages_including_title": 20,
        "max_pdf_bytes": 2097152,
        "saved": False,
    }
    async with env.factory() as database:
        before = {
            model: await database.scalar(select(func.count()).select_from(model))
            for model in (AssistantTurn, AuditEvent, StudentGoalState, CourseWorkspaceState)
        }
    response = await env.client.post(env.url + "/pdf", json=body())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="navox-own-work.pdf"'
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert PRIVATE_TEXT in extracted(tmp_path, response.content)
    async with env.factory() as database:
        after = {
            model: await database.scalar(select(func.count()).select_from(model))
            for model in before
        }
    assert after == before


@pytest.mark.asyncio
async def test_default_off_origin_and_cross_owner(export_env):
    env = export_env
    assert Settings(_env_file=None).student_work_export_enabled is False
    disabled = env.settings.model_copy(update={"student_work_export_enabled": False})
    env.app.dependency_overrides[get_settings] = lambda: disabled
    for suffix in ("/pdf", "/context"):
        result = await (
            env.client.post(env.url + suffix, json=body())
            if suffix == "/pdf"
            else env.client.get(env.url + suffix)
        )
        assert result.status_code == 404 and result.headers["cache-control"] == "private, no-store"
    env.app.dependency_overrides[get_settings] = lambda: env.settings
    response = await env.client.post(
        env.url + "/pdf", json=body(), headers={"Origin": "https://other.example"}
    )
    assert response.status_code == 403
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as stranger:
        assert (await stranger.get(env.url + "/context")).status_code == 401
        await stranger.post(
            "/api/v1/auth/register",
            json={
                "email": "stranger@example.com",
                "password": "twelve-character-password",
                "display_name": "Stranger",
            },
        )
        assert (await stranger.get(env.url + "/context")).status_code == 404
        assert (await stranger.post(env.url + "/pdf", json=body())).status_code == 404


@pytest.mark.asyncio
async def test_safe_bounded_invalid_and_stale_requests(export_env):
    env = export_env
    for changes, status in (
        ({"title": PRIVATE_TEXT, "confirmed_own_work": False}, 422),
        ({"generation": 1}, 409),
        ({"course_revision": 2}, 409),
        ({"course_label": "Invented course"}, 409),
        ({"text": "漢字"}, 422),
        ({"text": "line\n" * 1000}, 422),
    ):
        response = await env.client.post(env.url + "/pdf", json=body(**changes))
        assert response.status_code == status, response.text
        assert PRIVATE_TEXT not in response.text and "漢字" not in response.text
        assert response.headers["cache-control"] == "private, no-store"
    response = await env.client.post(
        env.url + "/pdf",
        content=b"x" * (128 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    duplicate = json.dumps(body())[:-1] + ',"text":"duplicate"}'
    assert (
        await env.client.post(
            env.url + "/pdf", content=duplicate, headers={"Content-Type": "application/json"}
        )
    ).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["context", "run_worker"])
async def test_unexpected_failures_are_private_and_content_free(
    export_env, monkeypatch, caplog, operation
):
    env = export_env

    async def unexpected(*_args, **_kwargs):
        raise RuntimeError(PRIVATE_TEXT)

    monkeypatch.setattr(service, operation, unexpected)
    response = (
        await env.client.get(env.url + "/context")
        if operation == "context"
        else await env.client.post(env.url + "/pdf", json=body())
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Student work export is temporarily unavailable"}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert PRIVATE_TEXT not in response.text and PRIVATE_TEXT not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["course", "workspace", "reset", "session", "flag"])
async def test_fresh_publication_fences(export_env, monkeypatch, change):
    env = export_env
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(_payload):
        entered.set()
        await release.wait()
        return b"%PDF-synthetic-should-not-be-published"

    monkeypatch.setattr(service, "run_worker", delayed)
    task = asyncio.create_task(env.client.post(env.url + "/pdf", json=body()))
    await asyncio.wait_for(entered.wait(), 3)
    async with env.factory() as database:
        if change == "course":
            await database.execute(
                update(StudentCourse)
                .where(StudentCourse.id == env.course_id)
                .values(revision=2, archived=True)
            )
        elif change == "workspace":
            database.add(
                CourseWorkspaceState(
                    course_id=env.course_id,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    encrypted_payload="Not read by export",
                )
            )
        elif change == "reset":
            # The actual full reset helper creates/increments the durable fence.
            await schedule.reset_data(
                database,
                env.user_id,
                env.workspace_id,
                revision=0,
                request_id=uuid4(),
                settings=env.settings,
            )
            # Recreate the same course identifier/revision to prove generation,
            # rather than course absence, rejects pre-reset editor content.
            database.add(
                StudentCourse(
                    id=env.course_id,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    code="ENG101",
                    name="Writing",
                    revision=1,
                    term_start=datetime(2026, 9, 1).date(),
                    term_end=datetime(2026, 12, 31).date(),
                )
            )
        elif change == "session":
            await database.execute(
                update(UserSession)
                .where(UserSession.user_id == env.user_id)
                .values(revoked_at=datetime.now(UTC))
            )
        elif change == "flag":
            env.settings.student_work_export_enabled = False
        await database.commit()
    release.set()
    response = await asyncio.wait_for(task, 3)
    assert response.status_code == {"session": 401, "flag": 404}.get(change, 409), response.text
    assert not response.content.startswith(b"%PDF-")
    if change == "reset":
        latest = await env.client.get(env.url + "/context")
        assert latest.json()["generation"] == 1
        assert (await env.client.post(env.url + "/pdf", json=body())).status_code == 409


@pytest.mark.asyncio
async def test_concurrency_is_bounded_and_slots_release(monkeypatch):
    monkeypatch.setattr(service, "SLOTS", asyncio.Semaphore(2))
    async with service.export_slot(), service.export_slot():
        with pytest.raises(service.ExportError) as failure:
            async with service.export_slot():
                pytest.fail("A third renderer must not enter")
        assert failure.value.status_code == 429
    assert service.SLOTS._value == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reset", "course_archive", "course_revision"])
async def test_postgres_publication_waits_for_owner_and_rechecks_committed_changes(
    export_env, monkeypatch, request, change
):
    env = export_env
    if env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires actual PostgreSQL User row locks and pg_blocking_pids")
    request.node.user_properties.append(("navox_actual_postgres_fixture", True))
    renderer_entered, release_renderer, final_owner_entered = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    original_owner = schedule.owner
    publication_pid = None
    writer = None

    async def delayed(_payload):
        renderer_entered.set()
        await release_renderer.wait()
        return b"%PDF-synthetic-must-not-be-published"

    async def observed_owner(database, user_id, workspace_id, *, lock=False):
        nonlocal publication_pid
        if lock and database is not writer and renderer_entered.is_set():
            publication_pid = await database.scalar(text("SELECT pg_backend_pid()"))
            final_owner_entered.set()
        return await original_owner(database, user_id, workspace_id, lock=lock)

    monkeypatch.setattr(service, "run_worker", delayed)
    monkeypatch.setattr(schedule, "owner", observed_owner)
    pending = asyncio.create_task(env.client.post(env.url + "/pdf", json=body()))
    try:
        await asyncio.wait_for(renderer_entered.wait(), 5)
        async with env.factory() as writer:
            # The valid initial export snapshot has released its lock. Own the
            # shared writer lock before permitting the final publication read.
            await original_owner(writer, env.user_id, env.workspace_id, lock=True)
            writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
            release_renderer.set()
            await asyncio.wait_for(final_owner_entered.wait(), 5)
            assert publication_pid is not None and publication_pid != writer_pid
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                blockers = await writer.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": publication_pid}
                )
                if writer_pid in blockers:
                    break
                assert asyncio.get_running_loop().time() < deadline, (
                    "Final PDF publication never waited for the owner's writer lock"
                )
                await asyncio.sleep(0.02)
            assert not pending.done()
            request.node.user_properties.append(("navox_owner_row_lock_wait_proven", True))
            if change == "reset":
                original_commit = writer.commit

                async def commit_reset_with_same_course_metadata():
                    # Recreate identical synthetic metadata in the reset's own
                    # transaction. After the wait, only the durable generation
                    # fence distinguishes this from the valid initial snapshot.
                    writer.add(
                        StudentCourse(
                            id=env.course_id,
                            user_id=env.user_id,
                            workspace_id=env.workspace_id,
                            code="ENG101",
                            name="Writing",
                            revision=1,
                            term_start=datetime(2026, 9, 1).date(),
                            term_end=datetime(2026, 12, 31).date(),
                        )
                    )
                    await original_commit()

                monkeypatch.setattr(writer, "commit", commit_reset_with_same_course_metadata)
                await schedule.reset_data(
                    writer,
                    env.user_id,
                    env.workspace_id,
                    revision=0,
                    request_id=uuid4(),
                    settings=env.settings,
                )
            else:
                await schedule.save_course(
                    writer,
                    env.user_id,
                    env.workspace_id,
                    CourseUpdate(
                        code="ENG101",
                        name="Writing",
                        term_start=datetime(2026, 9, 1).date(),
                        term_end=datetime(2026, 12, 31).date(),
                        revision=1,
                        archived=change == "course_archive",
                    ),
                    env.course_id,
                )
        response = await asyncio.wait_for(pending, 5)
    finally:
        release_renderer.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Restore this course before formatting your work"
        if change == "course_archive"
        else "Your course or student data changed while formatting. Review a fresh export"
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert not response.content.startswith(b"%PDF-")
    assert PRIVATE_TEXT not in response.text
