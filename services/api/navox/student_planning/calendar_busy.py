"""Calendar busy intervals for reviewed study plans; never writes provider events."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import get_settings
from navox.db.models import Connection
from navox.providers.google_availability import GoogleAvailabilityProvider
from navox.providers.google_oauth import GoogleAccessTokenError, access_token_for_connection
from navox.student import service as schedule
from navox.student_calendars import service as calendars
from navox.student_calendars.schemas import CONSENT_VERSION


@dataclass(frozen=True)
class CalendarBusy:
    intervals: tuple[tuple[datetime, datetime], ...]
    digest: str
    connected: bool
    warnings: tuple[str, ...]


async def inventory(
    database: AsyncSession, user_id: UUID, workspace_id: UUID
) -> list[dict[str, Any]]:
    state = await calendars.state(database, user_id, workspace_id)
    result = []
    for grant in await calendars.grants(database, user_id, workspace_id):
        if grant.status == "revoked":
            continue
        connection = await calendars.owned_connection(
            database, user_id, workspace_id, grant.connection_id
        )
        if (
            state is None
            or grant.owner_scope_id != state.id
            or grant.generation != state.generation
            or grant.consent_version != CONSENT_VERSION
        ):
            raise schedule.StudentError(
                409, "Review calendar access in Connections before planning"
            )
        result.append(
            {
                "id": str(grant.id),
                "revision": grant.revision,
                "status": grant.status,
                "generation": state.generation,
                "state_revision": state.revision,
                "connection_id": str(connection.id) if connection else None,
                "credential_id": str(connection.credential_reference) if connection else None,
                "connection_status": connection.status if connection else None,
                "scopes": sorted(connection.granted_scopes) if connection else [],
            }
        )
    return sorted(result, key=lambda row: row["id"])


async def collect(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    start_at: datetime,
    end_at: datetime,
    travel_minutes: int,
) -> CalendarBusy:
    original = await inventory(database, user_id, workspace_id)
    if not original:
        return CalendarBusy(
            (),
            "unconnected",
            False,
            (
                "No calendar is connected. This preview uses your saved classes, "
                "shifts and routine only.",
            ),
        )
    if len(original) > 8:
        raise schedule.StudentError(409, "Narrow calendar connections before planning")
    user = await schedule.owner(database, user_id, workspace_id)
    if user.agent_paused:
        raise schedule.StudentError(
            409, "Resume NavoX to check connected calendars before planning"
        )
    provider = GoogleAvailabilityProvider()
    intervals: list[tuple[datetime, datetime]] = []

    async def check() -> None:
        if await inventory(database, user_id, workspace_id) != original:
            raise schedule.StudentError(409, "Calendar access changed. Review a new plan")

    try:
        async with asyncio.timeout(45):
            for grant in original:
                connection = await calendars.owned_connection(
                    database,
                    user_id,
                    workspace_id,
                    UUID(grant["connection_id"]) if grant["connection_id"] else None,
                )
                if grant["status"] != "active" or not calendars.ready(connection):
                    raise schedule.StudentError(
                        409, "Reconnect your calendar before planning; its free time is unknown"
                    )
                assert connection is not None
                # Token refresh metadata stays detached. Preview never commits another
                # transaction or changes permissions while gathering provider facts.
                detached = Connection(
                    id=connection.id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    provider="google",
                    status="active",
                    external_account_id=connection.external_account_id,
                    external_email=connection.external_email,
                    granted_scopes=list(connection.granted_scopes),
                    credential_reference=connection.credential_reference,
                )
                token = await access_token_for_connection(
                    database, connection=detached, settings=get_settings()
                )
                if not calendars.ready(detached):
                    raise schedule.StudentError(
                        409, "Calendar permissions changed. Reconnect before planning"
                    )
                value = await provider.collect(
                    access_token=token,
                    start_at=start_at - timedelta(minutes=travel_minutes),
                    end_at=end_at + timedelta(minutes=travel_minutes),
                    check=check,
                )
                if value.state != "complete":
                    raise schedule.StudentError(
                        409, "Your calendar could not be fully checked. Try planning again"
                    )
                intervals.extend(
                    (
                        row.start_at - timedelta(minutes=travel_minutes),
                        row.end_at + timedelta(minutes=travel_minutes),
                    )
                    for row in value.busy
                )
            await check()
    except (GoogleAccessTokenError, TimeoutError):
        raise schedule.StudentError(
            503, "Your calendar could not be checked. Reconnect or try again"
        ) from None
    values = tuple(sorted(set(intervals)))
    digest = hashlib.sha256(
        json.dumps(
            {"access": original, "busy": [[a.isoformat(), b.isoformat()] for a, b in values]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return CalendarBusy(
        values,
        digest,
        True,
        (f"Calendar meetings include your {travel_minutes}-minute travel buffer on both sides.",),
    )
