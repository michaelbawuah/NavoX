from typing import Any

import pytest
from fastapi.testclient import TestClient

from navox.api import health
from navox.api.main import app


def test_liveness_is_available_without_dependencies() -> None:
    response = TestClient(app).get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "live"


def test_readiness_reports_ready_when_dependencies_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ready(_: Any) -> bool:
        return True

    monkeypatch.setattr(health, "check_database", ready)
    monkeypatch.setattr(health, "check_temporal", ready)

    response = TestClient(app).get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_fails_closed_when_a_dependency_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def database_ready(_: Any) -> bool:
        return True

    async def temporal_unavailable(_: Any) -> bool:
        return False

    monkeypatch.setattr(health, "check_database", database_ready)
    monkeypatch.setattr(health, "check_temporal", temporal_unavailable)

    response = TestClient(app).get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["checks"]["temporal"] is False
