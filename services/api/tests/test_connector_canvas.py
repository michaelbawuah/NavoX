from uuid import UUID

import httpx
import pytest

from navox.connectors.builtin.canvas import CanvasConnector, validate_canvas_base_url
from navox.connectors.contracts import ConnectorRuntimeError, SyncRequest


class Secrets:
    @property
    def names(self) -> frozenset[str]:
        return frozenset({"CANVAS_API_TOKEN"})

    def get(self, name: str) -> str:
        assert name == "CANVAS_API_TOKEN"
        return "canvas-test-token"


@pytest.mark.asyncio
async def test_canvas_read_only_sync_normalizes_academic_resources() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer canvas-test-token"
        path = request.url.path
        if path == "/api/v1/courses":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 42,
                        "name": "ECE 2300",
                        "course_code": "ECE2300",
                        "workflow_state": "available",
                    }
                ],
            )
        if path == "/api/v1/courses/42/assignments":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 7,
                        "name": "Problem Set 4",
                        "description": "<p>Complete problems 1-5.</p>",
                        "due_at": "2026-10-01T23:59:00Z",
                        "updated_at": "2026-09-24T12:00:00Z",
                        "published": True,
                        "html_url": "https://canvas.example.edu/courses/42/assignments/7",
                        "submission": {
                            "workflow_state": "unsubmitted",
                            "missing": False,
                            "late": False,
                        },
                    }
                ],
            )
        if path == "/api/v1/announcements":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9,
                        "title": "Exam room",
                        "message": "<p>The exam is in Phillips 101.</p>",
                        "context_code": "course_42",
                        "posted_at": "2026-09-24T13:00:00Z",
                        "html_url": "https://canvas.example.edu/courses/42/discussion_topics/9",
                    }
                ],
            )
        if path == "/api/v1/calendar_events":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 11,
                        "title": "Prelim review",
                        "description": "<p>Review session.</p>",
                        "context_code": "course_42",
                        "start_at": "2026-09-29T18:00:00Z",
                        "end_at": "2026-09-29T19:00:00Z",
                        "html_url": "https://canvas.example.edu/calendar?event_id=11",
                    }
                ],
            )
        raise AssertionError(f"unexpected path: {path}")

    connector = CanvasConnector(
        {"base_url": "https://canvas.example.edu"},
        Secrets(),
        transport=httpx.MockTransport(handler),
    )
    page = await connector.sync(
        SyncRequest(
            connection_id=UUID("11111111-1111-4111-8111-111111111111"),
            workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
            capabilities=frozenset(
                {
                    "academic.courses.read",
                    "academic.assignments.read",
                    "academic.submissions.read",
                    "academic.announcements.read",
                    "calendar.events.read",
                }
            ),
        )
    )

    assert {item.resource_type for item in page.resources} == {
        "academic.course",
        "academic.assignment",
        "academic.announcement",
        "calendar.event",
    }
    assignment = next(
        item for item in page.resources if item.resource_type == "academic.assignment"
    )
    assert assignment.canonical["subject"] == "Problem Set 4"
    assert "Complete problems 1-5." in str(assignment.canonical["content"])
    assert assignment.canonical["metadata"]["submission_state"] == "unsubmitted"
    assert page.has_more is False


@pytest.mark.asyncio
async def test_canvas_pagination_cannot_escape_configured_origin() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[],
            headers={
                "Link": '<https://evil.example/api/v1/courses?page=2>; rel="next"',
            },
        )

    connector = CanvasConnector(
        {"base_url": "https://canvas.example.edu"},
        Secrets(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ConnectorRuntimeError, match="configured origin"):
        await connector.sync(
            SyncRequest(
                connection_id=UUID("11111111-1111-4111-8111-111111111111"),
                workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
                capabilities=frozenset({"academic.courses.read"}),
            )
        )


@pytest.mark.parametrize(
    "value",
    [
        "http://canvas.example.edu",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://user:pass@canvas.example.edu",
        "https://canvas.example.edu/api/v1",
    ],
)
def test_canvas_base_url_rejects_unsafe_origins(value: str) -> None:
    with pytest.raises(ValueError):
        validate_canvas_base_url(value)
