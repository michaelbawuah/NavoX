from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from navox.db.models import Connection
from navox.providers.google_availability import GoogleAvailabilitySnapshot, ProviderBusy
from navox.student import service as schedule
from navox.student_calendars.schemas import AVAILABILITY_SCOPES
from navox.student_planning import calendar_busy


@pytest.mark.asyncio
@pytest.mark.parametrize("coverage", ["complete", "partial", "unavailable"])
async def test_planner_calendar_adapter_requires_complete_coverage(monkeypatch, coverage):
    user_id, workspace_id, connection_id = uuid4(), uuid4(), uuid4()
    original = [{"id": str(uuid4()), "status": "active", "connection_id": str(connection_id)}]

    async def inventory(*args):
        return original

    async def owner(*args):
        return SimpleNamespace(agent_paused=False)

    connection = Connection(
        id=connection_id,
        user_id=user_id,
        workspace_id=workspace_id,
        provider="google",
        status="active",
        granted_scopes=list(AVAILABILITY_SCOPES),
        credential_reference=uuid4(),
        external_account_id="test",
        external_email="qa@example.test",
    )

    async def owned(*args):
        return connection

    async def token(*args, **kwargs):
        return "synthetic-token"

    now = datetime(2026, 10, 24, 12, tzinfo=UTC)

    class Provider:
        async def collect(self, **kwargs):
            await kwargs["check"]()
            return GoogleAvailabilitySnapshot(
                coverage, 1, (ProviderBusy(now, now + timedelta(hours=1)),), now
            )

    monkeypatch.setattr(calendar_busy, "inventory", inventory)
    monkeypatch.setattr(calendar_busy.schedule, "owner", owner)
    monkeypatch.setattr(calendar_busy.calendars, "owned_connection", owned)
    monkeypatch.setattr(calendar_busy, "access_token_for_connection", token)
    monkeypatch.setattr(calendar_busy, "GoogleAvailabilityProvider", Provider)
    if coverage != "complete":
        with pytest.raises(schedule.StudentError, match="fully checked"):
            await calendar_busy.collect(
                None,
                user_id,
                workspace_id,
                start_at=now,
                end_at=now + timedelta(days=1),
                travel_minutes=15,
            )
    else:
        value = await calendar_busy.collect(
            None,
            user_id,
            workspace_id,
            start_at=now,
            end_at=now + timedelta(days=1),
            travel_minutes=15,
        )
        assert value.intervals == (
            (now - timedelta(minutes=15), now + timedelta(hours=1, minutes=15)),
        )
        assert value.connected
