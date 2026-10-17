"""Private course workspace boundaries and real bounded local PDF extraction."""

import asyncio
import hashlib
import shutil
import sys
import zlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select

from navox.api import coursework
from navox.api.main import create_app
from navox.connectors.builtin.google_gmail import GMAIL_MANIFEST
from navox.core.settings import Settings, get_settings
from navox.coursework import service, uploads
from navox.coursework.schemas import MAX_UPLOAD_BYTES, OverviewUpdate
from navox.coursework.uploads import UploadError, extract_material, read_upload, upload_slot
from navox.db.coursework import (
    CourseEmailLink,
    CourseMaterial,
    CourseworkCommand,
    CourseWorkspaceState,
)
from navox.db.models import (
    Commitment,
    CommitmentSource,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
)
from navox.db.session import get_database_session
from navox.db.student import StudentCourse
from navox.db.student_planning import StudentTask
from navox.providers.google_sources import GMAIL_READ_SCOPE


@pytest_asyncio.fixture
async def env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
    ).model_copy(update={"student_coursework_enabled": True})

    async def database():
        async with ai_database() as session:
            yield session

    app = create_app()
    app.include_router(coursework.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "coursework@example.com",
                "password": "twelve-character-password",
                "display_name": "Student",
            },
        )
        assert response.status_code == 201
        account = response.json()
        course = await client.post(
            "/api/v1/student/courses",
            json={
                "code": "CS 2110",
                "name": "Programming",
                "term_start": "2026-08-20",
                "term_end": "2026-12-20",
            },
        )
        assert course.status_code == 201, course.text
        course_id = UUID(course.json()["id"])
        yield SimpleNamespace(
            client=client,
            app=app,
            factory=ai_database,
            settings=settings,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
            course_id=course_id,
            base=f"/api/v1/student/courses/{course_id}/workspace",
            monkeypatch=monkeypatch,
        )


async def upload(
    env,
    text="Private notes about hash tables.",
    *,
    kind="note",
    title="Lecture notes",
    request_id=None,
    media_type="text/markdown",
    material_id=None,
    revision=0,
):
    path = f"{env.base}/materials" + (f"/{material_id}" if material_id else "")
    method = "PUT" if material_id else "POST"
    response = await env.client.request(
        method,
        path,
        params={"kind": kind, "revision": revision},
        content=text.encode() if isinstance(text, str) else text,
        headers={
            "Content-Type": media_type,
            "X-Material-Title": quote(title, safe=""),
            "X-Request-ID": str(request_id or uuid4()),
        },
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()


def pdf(pages=1, text="AI may be used for brainstorming only.", image=False):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for _index in range(pages):
        page_id = len(objects) + 1
        stream_id = page_id + 1
        kids.append(f"{page_id} 0 R")
        if image:
            image_id = page_id + 2
            objects.append(
                (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /XObject << /Im0 {image_id} 0 R >> >> "
                    f"/Contents {stream_id} 0 R >>"
                ).encode()
            )
            content = b"q 72 0 0 72 72 650 cm /Im0 Do Q"
        else:
            objects.append(
                (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
                    f"/BaseFont /Helvetica >> >> >> /Contents {stream_id} 0 R >>"
                ).encode()
            )
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"
        )
        if image:
            compressed = zlib.compress(b"\xff\xff\xff")
            objects.append(
                (
                    "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
                    "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                    f"/Length {len(compressed)} >>\nstream\n"
                ).encode()
                + compressed
                + b"\nendstream"
            )
    objects[1] = f"<< /Type /Pages /Count {pages} /Kids [{' '.join(kids)}] >>".encode()
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


@pytest.mark.asyncio
async def test_unconfigured_and_unauthenticated_never_read_uploaded_body(env, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Disabled or unauthenticated uploads must not be read")

    monkeypatch.setattr(coursework, "read_upload", forbidden)
    env.settings.__dict__["student_coursework_enabled"] = False
    response = await env.client.post(
        f"{env.base}/materials",
        content=b"secret",
        headers={
            "Content-Type": "text/plain",
            "X-Material-Title": "Notes",
            "X-Request-ID": str(uuid4()),
        },
    )
    assert response.status_code == 503
    env.client.cookies.clear()
    assert (await env.client.get(f"{env.base}/overview")).status_code == 401


@pytest.mark.asyncio
async def test_empty_reads_create_no_private_state_and_call_no_models(env, monkeypatch):
    from navox.ai import configured

    async def forbidden(*args, **kwargs):
        pytest.fail("Course reads must not build an AI runtime")

    monkeypatch.setattr(configured, "build_runtime", forbidden)
    response = await env.client.get(f"{env.base}/overview")
    assert response.status_code == 200
    assert response.json()["policy_status"] == "unknown"
    assert response.json()["ai_permission_granted"] is False
    assert response.json()["default_help_mode"] == "tutor_outline_review"
    assert "no-store" in response.headers["cache-control"]
    assert (await env.client.get(f"{env.base}/materials")).json() == {"materials": []}
    async with env.factory() as database:
        for model in (CourseMaterial, CourseWorkspaceState, CourseworkCommand):
            assert await database.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_private_note_encrypted_and_download_is_attachment_not_active_html(env):
    text = "<script>alert('private')</script>\nHash table notes."
    receipt = await upload(env, text, title="Résumé – notes")
    identifier = receipt["entity_id"]
    detail = await env.client.get(f"{env.base}/materials/{identifier}")
    assert detail.status_code == 200 and detail.json()["pages"] == [text]
    assert detail.json()["title"] == "Résumé – notes"
    download = await env.client.get(f"{env.base}/materials/{identifier}/file")
    assert download.content == text.encode()
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-security-policy"] == "sandbox"
    async with env.factory() as database:
        row = await database.get(CourseMaterial, UUID(identifier))
        assert "Hash table" not in row.encrypted_payload and "Résumé" not in row.encrypted_payload
        assert row.content_hash == hashlib.sha256(text.encode()).hexdigest()
        commands = list(await database.scalars(select(CourseworkCommand)))
        assert all("Hash table" not in str(row.result) for row in commands)


@pytest.mark.asyncio
async def test_request_receipts_prevent_duplicate_saves_and_do_not_resurrect_forgotten_text(env):
    request_id = uuid4()
    first = await upload(env, request_id=request_id)
    second = await upload(env, request_id=request_id)
    assert first["entity_id"] == second["entity_id"] and second["reused"] is True
    removed = await env.client.request(
        "DELETE",
        f"{env.base}/materials/{first['entity_id']}",
        json={"revision": 1, "request_id": str(uuid4())},
    )
    assert removed.status_code == 200
    replay = await upload(env, request_id=request_id)
    assert replay["reused"] is True and replay["available"] is False
    assert (await env.client.get(f"{env.base}/materials")).json() == {"materials": []}
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(CourseMaterial)) == 0
        assert all(
            "Private notes" not in str(row.result)
            for row in await database.scalars(select(CourseworkCommand))
        )


@pytest.mark.asyncio
async def test_stale_material_edit_and_reused_id_with_different_content_are_rejected(env):
    request_id = uuid4()
    first = await upload(env, request_id=request_id)
    for method, path, revision, text, rid in [
        ("PUT", f"/{first['entity_id']}", 0, b"Changed", uuid4()),
        ("POST", "", 0, b"Different", request_id),
    ]:
        response = await env.client.request(
            method,
            f"{env.base}/materials{path}",
            params={"revision": revision},
            content=text,
            headers={
                "Content-Type": "text/plain",
                "X-Material-Title": "Notes",
                "X-Request-ID": str(rid),
            },
        )
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_materials_and_search_are_strictly_selected_course_and_owner(env):
    receipt = await upload(env, "Unique collision explanation")
    other = await env.client.post(
        "/api/v1/student/courses",
        json={
            "code": "MATH",
            "name": "Other course",
            "term_start": "2026-08-20",
            "term_end": "2026-12-20",
        },
    )
    other_base = f"/api/v1/student/courses/{other.json()['id']}/workspace"
    assert (await env.client.get(f"{other_base}/search?q=collision")).json()["results"] == []
    assert (
        await env.client.get(f"{other_base}/materials/{receipt['entity_id']}")
    ).status_code == 404
    own = await env.client.get(f"{env.base}/search?q=collision")
    assert own.json()["results"][0]["material_id"] == receipt["entity_id"]
    env.client.cookies.clear()
    await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "other-coursework@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    assert (await env.client.get(f"{env.base}/materials")).status_code == 404


@pytest.mark.asyncio
async def test_exact_multiline_syllabus_quote_and_sources_are_cleared_on_delete(env):
    text = "Office hours: Tue 2pm.\nAI may be used for\nbrainstorming only."
    material = await upload(env, text, kind="syllabus")
    source = {
        "material_id": material["entity_id"],
        "revision": 1,
        "page": 1,
        "quote": "AI may be used for\nbrainstorming only.",
    }
    payload = {
        "revision": 0,
        "request_id": str(uuid4()),
        "staff": [
            {
                "name": "Prof. Review",
                "role": "Professor",
                "office_hours": "Tue 2pm",
                "source": {**source, "quote": "Office hours: Tue 2pm."},
            }
        ],
        "grade_breakdown": [],
        "ai_policy": source,
    }
    response = await env.client.put(f"{env.base}/overview", json=payload)
    assert response.status_code == 200, response.text
    overview = (await env.client.get(f"{env.base}/overview")).json()
    assert overview["ai_policy"]["quote"] == source["quote"]
    assert (
        overview["policy_status"] == "quoted_policy" and overview["ai_permission_granted"] is False
    )
    await env.client.request(
        "DELETE",
        f"{env.base}/materials/{material['entity_id']}",
        json={"revision": 1, "request_id": str(uuid4())},
    )
    overview = (await env.client.get(f"{env.base}/overview")).json()
    assert overview["ai_policy"] is None and overview["staff"] == []
    assert overview["revision"] == 2
    assert (await env.client.get(f"{env.base}/search?q=brainstorming")).json()["results"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["quote", "page", "revision", "kind"])
async def test_policy_source_must_be_current_exact_syllabus_page(env, change):
    material = await upload(
        env, "No generated submissions.", kind="note" if change == "kind" else "syllabus"
    )
    source = {
        "material_id": material["entity_id"],
        "revision": 2 if change == "revision" else 1,
        "page": 2 if change == "page" else 1,
        "quote": "Made-up permission" if change == "quote" else "No generated submissions.",
    }
    response = await env.client.put(
        f"{env.base}/overview",
        json={"revision": 0, "request_id": str(uuid4()), "ai_policy": source},
    )
    assert response.status_code == (409 if change == "revision" else 422)


@pytest.mark.asyncio
async def test_material_update_clears_old_policy_and_requires_new_quote(env):
    material = await upload(env, "Old course policy.", kind="syllabus")
    response = await env.client.put(
        f"{env.base}/overview",
        json={
            "revision": 0,
            "request_id": str(uuid4()),
            "ai_policy": {
                "material_id": material["entity_id"],
                "revision": 1,
                "page": 1,
                "quote": "Old course policy.",
            },
        },
    )
    assert response.status_code == 200
    await upload(
        env, "New course policy.", kind="syllabus", material_id=material["entity_id"], revision=1
    )
    current = (await env.client.get(f"{env.base}/overview")).json()
    assert current["ai_policy"] is None and current["revision"] == 2


@pytest.mark.asyncio
async def test_archived_course_history_readable_and_mutations_refused(env):
    receipt = await upload(env)
    async with env.factory() as database:
        (await database.get(StudentCourse, env.course_id)).archived = True
        await database.commit()
    assert (await env.client.get(f"{env.base}/overview")).json()["archived"] is True
    assert (await env.client.get(f"{env.base}/materials/{receipt['entity_id']}")).status_code == 200
    assert (await env.client.get(f"{env.base}/search?q=Private")).status_code == 200
    response = await env.client.put(
        f"{env.base}/overview", json={"revision": 0, "request_id": str(uuid4())}
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_manual_assignments_are_selected_course_only(env):
    async with env.factory() as database:
        database.add(
            StudentTask(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                course_id=env.course_id,
                title="Saved assignment",
                estimated_minutes=60,
                remaining_minutes=30,
                actual_minutes=0,
                difficulty=3,
                priority=3,
                course_weight=1,
                status="active",
                revision=1,
            )
        )
        database.add(
            StudentTask(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                course_id=None,
                title="Unrelated private task",
                estimated_minutes=60,
                remaining_minutes=30,
                actual_minutes=0,
                difficulty=3,
                priority=3,
                course_weight=1,
                status="active",
                revision=1,
            )
        )
        await database.commit()
    response = await env.client.get(f"{env.base}/assignments")
    assert [item["title"] for item in response.json()["tasks"]] == ["Saved assignment"]


async def email(env):
    async with env.factory() as database:
        definition = ConnectorDefinition(
            id=uuid4(),
            connector_key=GMAIL_MANIFEST.id,
            version=GMAIL_MANIFEST.version,
            display_name=GMAIL_MANIFEST.display_name,
            connector_class=GMAIL_MANIFEST.connector_class,
            trust_level="NAVOX_FIRST_PARTY",
            manifest=GMAIL_MANIFEST.model_dump(mode="json", by_alias=True),
            active=True,
        )
        database.add(definition)
        await database.flush()
        connection = ConnectorConnection(
            id=uuid4(),
            connector_definition_id=definition.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="coursework-mail",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=["communication.messages.read"],
            provider_capabilities=["communication.messages.read"],
        )
        database.add(connection)
        await database.flush()
        resource = ConnectorResource(
            id=uuid4(),
            workspace_id=env.workspace_id,
            connector_connection_id=connection.id,
            provider="google",
            resource_type="communication.message",
            external_id="18a2d9380fe6c4b1",
            content_hash="a" * 64,
            canonical={"status": "active"},
            retrieved_at=datetime.now(UTC),
        )
        database.add(resource)
        await database.commit()
        return resource.id, connection.id


@pytest.mark.asyncio
async def test_email_link_is_explicit_source_pinned_and_hidden_after_revocation(env):
    source_id, connection_id = await email(env)
    payload = {"source_id": str(source_id), "source_hash": "a" * 64, "request_id": str(uuid4())}
    linked = await env.client.post(f"{env.base}/email-links", json=payload)
    assert linked.status_code == 201, linked.text
    listed = (await env.client.get(f"{env.base}/email-links")).json()
    assert (
        listed["links"][0]["source_open_url"]
        == "https://mail.google.com/mail/u/0/#all/18a2d9380fe6c4b1"
    )
    assert "subject" not in listed["links"][0]
    async with env.factory() as database:
        (await database.get(ConnectorConnection, connection_id)).authorized_capabilities = []
        await database.commit()
    unavailable = (await env.client.get(f"{env.base}/email-links")).json()
    assert unavailable == {"links": [], "unavailable_count": 1}


@pytest.mark.asyncio
async def test_material_count_quota_and_envelope_scope_cannot_be_bypassed(env, monkeypatch):
    monkeypatch.setattr(service, "MAX_MATERIALS_COURSE", 1)
    receipt = await upload(env)
    response = await env.client.post(
        f"{env.base}/materials",
        content=b"Another note",
        headers={
            "Content-Type": "text/plain",
            "X-Material-Title": "Notes",
            "X-Request-ID": str(uuid4()),
        },
    )
    assert response.status_code == 409
    async with env.factory() as database:
        row = await database.get(CourseMaterial, UUID(receipt["entity_id"]))
        row.course_id = uuid4()
        with pytest.raises(service.CourseworkError):
            service.reveal(row, env.settings, "material")
        await database.rollback()


@pytest.mark.asyncio
async def test_owner_forget_helper_clears_all_private_copies_and_no_other_course(env):
    await upload(env)
    async with env.factory() as database:
        await service.clear_coursework_data(
            database, user_id=env.user_id, workspace_id=env.workspace_id
        )
        await database.commit()
        for model in (CourseMaterial, CourseWorkspaceState, CourseworkCommand, CourseEmailLink):
            assert await database.scalar(select(func.count()).select_from(model)) == 0
        assert await database.get(StudentCourse, env.course_id) is not None


@pytest.mark.asyncio
async def test_upload_size_mime_and_chunked_limits(env):
    async def body():
        yield b"x" * (MAX_UPLOAD_BYTES // 2)
        yield b"y" * (MAX_UPLOAD_BYTES // 2 + 1)

    with pytest.raises(UploadError):
        await read_upload(body())
    for raw, mime, status in [
        (b"x", "text/html", 415),
        (b"\xff", "text/plain", 422),
        (b"%PDF-not a real PDF", "application/pdf", 422),
        (b" ", "text/plain", 422),
    ]:
        with pytest.raises(UploadError) as error:
            await extract_material(raw, mime)
        assert error.value.status_code == status


@pytest.mark.asyncio
async def test_busy_upload_rejects_before_reading_body(env, monkeypatch):
    monkeypatch.setattr(uploads, "UPLOAD_SLOTS", asyncio.Semaphore(1))

    async def forbidden(*args, **kwargs):
        pytest.fail("Busy uploads must not read another body")

    monkeypatch.setattr(coursework, "read_upload", forbidden)
    async with upload_slot():
        response = await env.client.post(
            f"{env.base}/materials",
            content=b"secret",
            headers={
                "Content-Type": "text/plain",
                "X-Material-Title": "Notes",
                "X-Request-ID": str(uuid4()),
            },
        )
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_missing_poppler_is_honest_and_does_not_claim_extraction(monkeypatch):
    monkeypatch.setattr(uploads.shutil, "which", lambda *args, **kwargs: None)
    with pytest.raises(UploadError, match="unavailable") as error:
        await extract_material(pdf(), "application/pdf")
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.skipif(
    shutil.which("pdftotext", path="/usr/bin:/bin") is None, reason="poppler-utils unavailable"
)
async def test_actual_local_pdf_text_image_only_and_101_page_limit():
    extracted = await extract_material(pdf(), "application/pdf")
    assert len(extracted.pages) == 1 and "brainstorming only" in extracted.pages[0]
    with pytest.raises(UploadError, match="no readable text"):
        await extract_material(pdf(image=True), "application/pdf")
    with pytest.raises(UploadError, match="100 pages"):
        await extract_material(pdf(pages=101), "application/pdf")


def test_grade_weights_and_private_source_contracts_are_bounded():
    with pytest.raises(ValidationError):
        OverviewUpdate(
            revision=0,
            request_id=uuid4(),
            grade_breakdown=[
                {"name": "Tests", "weight_percent": 80},
                {"name": "Homework", "weight_percent": 30},
            ],
        )


@pytest.mark.asyncio
async def test_private_json_errors_never_echo_inputs_and_all_writes_are_bounded(env):
    sentinel = "PRIVATE-SYLLABUS-QUOTE-DO-NOT-ECHO"
    response = await env.client.put(
        f"{env.base}/overview",
        json={
            "revision": 0,
            "request_id": str(uuid4()),
            "grade_breakdown": [
                {"name": "Tests", "weight_percent": 80},
                {"name": "Labs", "weight_percent": 30},
            ],
            "ai_policy": {"material_id": str(uuid4()), "revision": 1, "page": 1, "quote": sentinel},
        },
    )
    assert response.status_code == 422 and sentinel not in response.text
    for method, path in [
        ("PUT", "/overview"),
        ("POST", "/email-links"),
        ("DELETE", f"/materials/{uuid4()}"),
        ("DELETE", f"/email-links/{uuid4()}"),
    ]:
        invalid = await env.client.request(method, env.base + path, json={"private": sentinel})
        assert invalid.status_code == 422 and sentinel not in invalid.text
        limit = 512 * 1024 if path == "/overview" else 8192
        oversized = await env.client.request(
            method,
            env.base + path,
            content=b" " * (limit + 1),
            headers={"Content-Type": "application/json"},
        )
        assert oversized.status_code == 413 and "no-store" in oversized.headers["cache-control"]
    malformed = await env.client.put(
        f"{env.base}/overview",
        content=b'{"private":"' + sentinel.encode(),
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 422 and sentinel not in malformed.text


@pytest.mark.asyncio
async def test_upload_metadata_errors_do_not_echo_private_titles(env):
    sentinel = "PRIVATE-TITLE-DO-NOT-ECHO"
    response = await env.client.post(
        f"{env.base}/materials",
        content=b"private",
        headers={
            "Content-Type": "text/plain",
            "X-Material-Title": sentinel * 200,
            "X-Request-ID": str(uuid4()),
        },
    )
    assert response.status_code == 422 and sentinel not in response.text


@pytest.mark.asyncio
async def test_individual_forget_works_when_disabled_and_receipt_quota_is_full(env, monkeypatch):
    material = await upload(env)
    source_id, _ = await email(env)
    link_payload = {
        "source_id": str(source_id),
        "source_hash": "a" * 64,
        "request_id": str(uuid4()),
    }
    linked = (await env.client.post(f"{env.base}/email-links", json=link_payload)).json()
    monkeypatch.setattr(service, "MAX_COMMANDS", 2)
    env.settings.__dict__["student_coursework_enabled"] = False
    for path, receipt in [
        (f"materials/{material['entity_id']}", material),
        (f"email-links/{linked['entity_id']}", linked),
    ]:
        body = {"revision": receipt["revision"], "request_id": str(uuid4())}
        erased = await env.client.request("DELETE", f"{env.base}/{path}", json=body)
        replay = await env.client.request("DELETE", f"{env.base}/{path}", json=body)
        assert erased.status_code == replay.status_code == 200
        assert replay.json()["reused"] and not replay.json()["available"]
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(CourseMaterial)) == 0
        assert await database.scalar(select(func.count()).select_from(CourseEmailLink)) == 0
        # Content-free creation receipts remain to prevent resurrection by old POSTs.
        assert await database.scalar(select(func.count()).select_from(CourseworkCommand)) == 4
    env.settings.__dict__["student_coursework_enabled"] = True
    replay = await env.client.post(f"{env.base}/email-links", json=link_payload)
    assert replay.status_code == 201 and replay.json()["reused"]
    assert not replay.json()["available"]
    blocked = await env.client.put(
        f"{env.base}/overview", json={"revision": 0, "request_id": str(uuid4())}
    )
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_email_search_is_explicit_bounded_current_metadata_and_hash_pinned(env):
    source_id, connection_id = await email(env)
    async with env.factory() as database:
        resource = await database.get(ConnectorResource, source_id)
        resource.canonical = {
            "status": "active",
            "subject": "CS 2110: Lab deadline",
            "sender_label": "Professor Example",
            "body": "hidden body",
        }
        await database.commit()
    assert (await env.client.get(f"{env.base}/email-candidates")).status_code == 422
    response = await env.client.get(f"{env.base}/email-candidates", params={"q": "CS 2110"})
    assert response.status_code == 200
    row = response.json()["candidates"][0]
    assert row == {
        "source_id": str(source_id),
        "source_hash": "a" * 64,
        "title": "CS 2110: Lab deadline",
        "sender_label": "Professor Example",
        "display_label": None,
    }
    assert "hidden body" not in response.text
    assert (await env.client.get(f"{env.base}/email-candidates?q=hidden")).json()[
        "candidates"
    ] == []
    async with env.factory() as database:
        (await database.get(ConnectorResource, source_id)).content_hash = "b" * 64
        await database.commit()
    stale = await env.client.post(
        f"{env.base}/email-links",
        json={
            "source_id": row["source_id"],
            "source_hash": row["source_hash"],
            "request_id": str(uuid4()),
        },
    )
    assert stale.status_code == 409
    async with env.factory() as database:
        (await database.get(ConnectorConnection, connection_id)).authorized_capabilities = []
        await database.commit()
    assert (await env.client.get(f"{env.base}/email-candidates?q=CS")).json()["candidates"] == []


@pytest.mark.asyncio
async def test_email_labels_are_fresh_and_private_external_ids_are_not_navigation_urls(env):
    source_id, _ = await email(env)
    linked = await env.client.post(
        f"{env.base}/email-links",
        json={"source_id": str(source_id), "source_hash": "a" * 64, "request_id": str(uuid4())},
    )
    assert linked.status_code == 201
    async with env.factory() as database:
        source = await database.get(ConnectorResource, source_id)
        source.external_id = "https://private.example/export?token=PRIVATE-SIGNED-TOKEN"
        source.canonical = {"status": "active", "subject": "Updated saved title"}
        await database.commit()
    listed = await env.client.get(f"{env.base}/email-links")
    row = listed.json()["links"][0]
    assert row["source_open_url"] is None and row["title"] == "Updated saved title"
    assert "PRIVATE-SIGNED-TOKEN" not in listed.text


@pytest.mark.asyncio
async def test_email_search_caps_scan_and_results_without_fetching_mailboxes(env, monkeypatch):
    source_id, connection_id = await email(env)
    async with env.factory() as database:
        source = await database.get(ConnectorResource, source_id)
        source.canonical = {"status": "active", "subject": "Needle old message"}
        source.retrieved_at = datetime(2020, 1, 1, tzinfo=UTC)
        for index in range(205):
            database.add(
                ConnectorResource(
                    id=uuid4(),
                    workspace_id=env.workspace_id,
                    connector_connection_id=connection_id,
                    provider="google",
                    resource_type="communication.message",
                    external_id=f"{index + 1000:016x}",
                    content_hash="b" * 64,
                    canonical={"status": "active", "subject": "Lecture announcement"},
                    retrieved_at=datetime.now(UTC) + timedelta(seconds=index),
                )
            )
        await database.commit()
    calls = 0
    original = service.email_source

    async def checked(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "email_source", checked)
    found = (await env.client.get(f"{env.base}/email-candidates?q=Lecture")).json()
    assert len(found["candidates"]) == 20 and found["limit_reached"] and calls == 20
    calls = 0
    beyond_scan = (await env.client.get(f"{env.base}/email-candidates?q=Needle")).json()
    assert beyond_scan["candidates"] == [] and beyond_scan["limit_reached"] and calls == 200


@pytest.mark.asyncio
async def test_real_gmail_locator_only_retention_is_not_expanded_for_course_search(env):
    source_id, _ = await email(env)
    async with env.factory() as database:
        source = await database.get(ConnectorResource, source_id)
        source.canonical = {
            "source_type": "gmail_message",
            "status": "active",
            "has_subject": True,
            "has_content": True,
        }
        source.provider_metadata = {
            "source_hash": "a" * 64,
            "content_persisted": False,
            "private_subject": "PRIVATE-UNAUTHORIZED-LABEL",
        }
        await database.commit()
    response = await env.client.get(f"{env.base}/email-candidates?q=PRIVATE")
    assert response.status_code == 200 and response.json()["candidates"] == []
    assert "without saved labels" in response.json()["notice"]
    assert "PRIVATE-UNAUTHORIZED-LABEL" not in response.text
    async with env.factory() as database:
        source = await database.get(ConnectorResource, source_id)
        assert (
            "subject" not in source.canonical
            and source.provider_metadata["content_persisted"] is False
        )


async def saved_email_item(env, source_id, connection_id, *, title="Submit CS 2110 lab"):
    async with env.factory() as database:
        legacy = Connection(
            id=uuid4(),
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id=str(uuid4()),
            status="active",
            granted_scopes=[GMAIL_READ_SCOPE],
        )
        database.add(legacy)
        await database.flush()
        connector = await database.get(ConnectorConnection, connection_id)
        connector.legacy_connection_id = legacy.id
        source = await database.get(ConnectorResource, source_id)
        item = Commitment(
            id=uuid4(),
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            commitment_type="task",
            title=title,
            status="confirmed",
            dedupe_key=uuid4().hex,
        )
        database.add(item)
        await database.flush()
        database.add(
            CommitmentSource(
                commitment_id=item.id,
                connection_id=legacy.id,
                provider="google",
                source_type="gmail_message",
                external_resource_id=source.external_id,
                source_metadata={"subject": "PRIVATE-OLD-SUBJECT", "body": "PRIVATE-BODY"},
            )
        )
        await database.commit()
        return item.id, legacy.id


@pytest.mark.asyncio
async def test_locator_only_email_search_uses_owned_saved_item_label_not_email_subject(env):
    source_id, connection_id = await email(env)
    item_id, legacy_id = await saved_email_item(env, source_id, connection_id)
    found = await env.client.get(f"{env.base}/email-candidates", params={"q": "CS 2110 lab"})
    assert found.status_code == 200
    candidate = found.json()["candidates"][0]
    assert candidate["display_label"] == "Saved item: Submit CS 2110 lab"
    assert candidate["title"] is None and candidate["sender_label"] is None
    assert "PRIVATE-OLD-SUBJECT" not in found.text and "PRIVATE-BODY" not in found.text
    linked = await env.client.post(
        f"{env.base}/email-links",
        json={
            "source_id": candidate["source_id"],
            "source_hash": candidate["source_hash"],
            "request_id": str(uuid4()),
        },
    )
    assert linked.status_code == 201
    async with env.factory() as database:
        (await database.get(Commitment, item_id)).title = "Updated saved lab task"
        await database.commit()
    listed = (await env.client.get(f"{env.base}/email-links")).json()["links"][0]
    assert (
        listed["display_label"] == "Saved item: Updated saved lab task" and listed["title"] is None
    )
    async with env.factory() as database:
        resource = await database.get(ConnectorResource, source_id)
        assert "subject" not in resource.canonical
        command = await database.scalar(select(CourseworkCommand))
        assert "saved lab" not in str(command.result)
        (await database.get(Connection, legacy_id)).granted_scopes = []
        await database.commit()
    assert (await env.client.get(f"{env.base}/email-candidates?q=lab")).json()["candidates"] == []
    assert (await env.client.get(f"{env.base}/email-links")).json()["unavailable_count"] == 1


@pytest.mark.asyncio
async def test_saved_item_labels_require_exact_current_mailbox_and_message_identity(env):
    source_id, connection_id = await email(env)
    item_id, legacy_id = await saved_email_item(env, source_id, connection_id)
    async with env.factory() as database:
        attachment = await database.scalar(select(CommitmentSource))
        attachment.external_resource_id = "18a2d9380fe6c4b2"  # Another message in the same mailbox.
        await database.commit()
    assert (await env.client.get(f"{env.base}/email-candidates?q=lab")).json()["candidates"] == []
    async with env.factory() as database:
        attachment = await database.scalar(select(CommitmentSource))
        attachment.external_resource_id = "18a2d9380fe6c4b1"
        attachment.connection_id = None  # An unbound source cannot identify a mailbox.
        await database.commit()
    assert (await env.client.get(f"{env.base}/email-candidates?q=lab")).json()["candidates"] == []
    async with env.factory() as database:
        attachment = await database.scalar(select(CommitmentSource))
        attachment.connection_id = legacy_id
        (await database.get(Commitment, item_id)).status = "rejected"
        await database.commit()
    assert (await env.client.get(f"{env.base}/email-candidates?q=lab")).json()["candidates"] == []


@pytest.mark.asyncio
async def test_email_search_checks_current_owner_and_pause_state(env):
    source_id, connection_id = await email(env)
    async with env.factory() as database:
        source = await database.get(ConnectorResource, source_id)
        source.canonical = {"status": "active", "subject": "Private lecture email"}
        (await database.get(ConnectorConnection, connection_id)).health_state = "PAUSED"
        await database.commit()
    assert (await env.client.get(f"{env.base}/email-candidates?q=lecture")).json()[
        "candidates"
    ] == []
    own_cookies = dict(env.client.cookies)
    env.client.cookies.clear()
    other = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "foreign-coursework-mail@example.com",
            "display_name": "Other",
            "password": "twelve-character-password",
        },
    )
    assert other.status_code == 201
    env.client.cookies.clear()
    env.client.cookies.update(own_cookies)
    async with env.factory() as database:
        connection = await database.get(ConnectorConnection, connection_id)
        connection.health_state = "CONNECTED"
        connection.user_id = UUID(other.json()["id"])
        await database.commit()
    response = await env.client.get(f"{env.base}/email-candidates?q=lecture")
    assert response.status_code == 200 and response.json()["candidates"] == []
    forbidden = await env.client.post(
        f"{env.base}/email-links",
        json={"source_id": str(source_id), "source_hash": "a" * 64, "request_id": str(uuid4())},
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_json_and_raw_body_deadlines_stop_slow_streams(monkeypatch):
    monkeypatch.setattr(uploads, "READ_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(coursework, "JSON_TIMEOUT_SECONDS", 0.01)

    async def slow():
        yield b" "
        await asyncio.Event().wait()

    with pytest.raises(UploadError) as raw_error:
        await read_upload(slow())
    assert raw_error.value.status_code == 408
    request = SimpleNamespace(headers={"content-type": "application/json"}, stream=slow)
    with pytest.raises(coursework.HTTPException) as json_error:
        await coursework.bounded_json(request, OverviewUpdate)
    assert json_error.value.status_code == 408


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "output_limit", "cancel"])
async def test_pdf_subprocess_limits_and_cancellation_kill_and_reap_owned_child(monkeypatch, mode):
    original = asyncio.create_subprocess_exec
    processes = []
    started = asyncio.Event()
    monkeypatch.setattr(uploads.shutil, "which", lambda *args, **kwargs: "/usr/bin/pdftotext")
    monkeypatch.setattr(uploads, "EXTRACT_TIMEOUT_SECONDS", 0.05 if mode == "timeout" else 5)
    monkeypatch.setattr(uploads, "MAX_OUTPUT_BYTES", 1024)

    async def child(*args, **kwargs):
        assert kwargs["start_new_session"] and kwargs["stderr"] == asyncio.subprocess.DEVNULL
        assert b"%PDF" not in str(args).encode()  # Uploads are stdin data, never argv.
        code = "import time;time.sleep(30)"
        if mode == "output_limit":
            code = (
                "import sys,time;sys.stdout.buffer.write(b'x'*20000);"
                "sys.stdout.flush();time.sleep(30)"
            )
        process = await original(sys.executable, "-I", "-c", code, **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(uploads.asyncio, "create_subprocess_exec", child)
    task = asyncio.create_task(extract_material(b"%PDF-test-input", "application/pdf"))
    await started.wait()
    if mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(UploadError) as error:
            await task
        assert error.value.status_code == (408 if mode == "timeout" else 422)
    assert processes[0].returncode is not None and processes[0].returncode < 0
