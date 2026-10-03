"""Transient class projection accepts only explicit, bounded meeting evidence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from navox.connectors.contracts import CanonicalResource, stable_resource_id
from navox.knowledge.class_schedule import class_navigation_url, transient_class_source_snapshot


def resource(
    *, provider: str, kind: str, external_id: str, title: str | None, metadata: dict
) -> CanonicalResource:
    connection_id = uuid4()
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    return CanonicalResource(
        resource_id=stable_resource_id(connection_id, kind, external_id),
        workspace_id=uuid4(),
        connector_connection_id=connection_id,
        provider=provider,
        resource_type=kind,
        external_id=external_id,
        canonical={"subject": title, "metadata": metadata},
        retrieved_at=now,
    )


def test_transient_provider_projection_keeps_course_identity_and_conflicting_times() -> None:
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    course = resource(
        provider="canvas",
        kind="academic.course",
        external_id="course:42",
        title="Economics 3120",
        metadata={"course_id": "42", "course_code": "ECON 3120"},
    )
    canvas = resource(
        provider="canvas",
        kind="calendar.event",
        external_id="event:1",
        title="ECON 3120 Lecture",
        metadata={
            "start_at": (now + timedelta(hours=2, minutes=30)).isoformat(),
            "end_at": (now + timedelta(hours=3, minutes=30)).isoformat(),
            "scheduling_updated_at": (now - timedelta(days=1)).isoformat(),
        },
    )
    google = resource(
        provider="google",
        kind="calendar.event",
        external_id="event:2",
        title="ECON 3120",
        metadata={
            "start_at": (now + timedelta(hours=3)).isoformat(),
            "end_at": (now + timedelta(hours=4)).isoformat(),
            "scheduling_updated_at": now.isoformat(),
            "location": "Room 201",
        },
    )
    result = transient_class_source_snapshot((course, canvas, google), now=now)
    assert result["complete"] is True
    assert len(result["courses"]) == 1
    assert len(result["events"]) == 2
    assert result["events"][1]["location"] == "Room 201"


def test_unidentified_cancellation_fails_closed_and_course_alone_has_no_meeting() -> None:
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    course = resource(
        provider="canvas",
        kind="academic.course",
        external_id="course:42",
        title="Economics 3120",
        metadata={"course_id": "42"},
    )
    cancelled = resource(
        provider="google",
        kind="calendar.event",
        external_id="event:2",
        title=None,
        metadata={"status": "cancelled"},
    )
    alone = transient_class_source_snapshot((course,), now=now)
    assert alone["complete"] is True and alone["events"] == []
    uncertain = transient_class_source_snapshot((course, cancelled), now=now)
    assert uncertain["complete"] is False


def test_navigation_requires_exact_upcoming_event_and_provider_origin() -> None:
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    google = resource(
        provider="google",
        kind="calendar.event",
        external_id="event:2",
        title="ECON 3120 Lecture",
        metadata={"start_at": (now + timedelta(hours=3)).isoformat()},
    ).model_copy(update={"source_url": "https://calendar.google.com/calendar/event?eid=2"})
    args = {
        "connection_id": google.connector_connection_id,
        "resource_id": google.resource_id,
        "canvas_base_url": None,
        "now": now,
    }
    assert class_navigation_url((google,), **args) == google.source_url
    assert class_navigation_url((google,), **{**args, "resource_id": uuid4()}) is None
    evil = google.model_copy(update={"source_url": "https://evil.example/calendar/event?eid=2"})
    assert class_navigation_url((evil,), **args) is None
    cancelled = google.model_copy(
        update={
            "canonical": {
                "subject": "ECON 3120 Lecture",
                "metadata": {
                    "start_at": (now + timedelta(hours=3)).isoformat(),
                    "status": "cancelled",
                },
            }
        }
    )
    assert class_navigation_url((cancelled,), **args) is None


def test_canvas_navigation_requires_configured_origin() -> None:
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    canvas = resource(
        provider="canvas",
        kind="calendar.event",
        external_id="event:1",
        title="ECON 3120 Lecture",
        metadata={"start_at": (now + timedelta(hours=2)).isoformat()},
    ).model_copy(update={"source_url": "https://canvas.example.edu/calendar?event_id=1"})
    args = {
        "connection_id": canvas.connector_connection_id,
        "resource_id": canvas.resource_id,
        "canvas_base_url": "https://canvas.example.edu",
        "now": now,
    }
    assert class_navigation_url((canvas,), **args) == canvas.source_url
    wrong_origin = canvas.model_copy(
        update={"source_url": "https://canvas.example.edu.evil.example/calendar?event_id=1"}
    )
    assert class_navigation_url((wrong_origin,), **args) is None
