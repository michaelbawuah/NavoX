"""Saved Noa appearance and real completed-study streak groundwork."""

from datetime import date, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.api.student import result, scope
from navox.api.student_planning import expected_routine_owner
from navox.connectors.sync_state import database_now, utc
from navox.db.noa import NoaSettings
from navox.db.student_planning import StudentStudyBlock
from navox.student import service

router = APIRouter(
    prefix="/student/noa", tags=["Noa"], dependencies=[Depends(expected_routine_owner)]
)


class Appearance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    look: Literal["girl", "boy"] = "girl"
    vibe: Literal["midnight", "lavender", "sunset", "matcha"] = "midnight"
    revision: int = Field(default=0, ge=0)


def study_streak(days: set[date], today: date) -> int:
    """One credit per local day; an unfinished today never breaks yesterday's streak."""
    cursor = today if today in days else today - timedelta(days=1)
    count = 0
    while cursor in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


@router.get("")
async def get_noa(
    account: CurrentAccountDependency, database: DatabaseSession, response: Response
) -> dict[str, object]:
    user_id, workspace_id = scope(account)
    user = await result(service.owner(database, user_id, workspace_id))
    response.headers["Cache-Control"] = "private, no-store"
    row = await database.scalar(
        select(NoaSettings).where(
            NoaSettings.user_id == user_id, NoaSettings.workspace_id == workspace_id
        )
    )
    moment = utc(await database_now(database))
    completed = await database.scalars(
        select(StudentStudyBlock.outcome_at).where(
            StudentStudyBlock.user_id == user_id,
            StudentStudyBlock.workspace_id == workspace_id,
            StudentStudyBlock.status == "done",
            # M2 credits recorded study completions. This is not a focus timer
            # or proof of uninterrupted elapsed work (the later Noa focus feature).
            StudentStudyBlock.actual_minutes >= 15,
            StudentStudyBlock.outcome_at.is_not(None),
            StudentStudyBlock.outcome_at <= moment,
        )
    )
    zone = ZoneInfo(user.timezone)
    days = {utc(value).astimezone(zone).date() for value in completed if value is not None}
    return {
        "look": row.look if row else "girl",
        "vibe": row.vibe if row else "midnight",
        "revision": row.revision if row else 0,
        "study_streak": study_streak(days, moment.astimezone(zone).date()),
        "credited_today": moment.astimezone(zone).date() in days,
        "timezone": user.timezone,
    }


@router.put("")
async def save_noa(
    payload: Appearance,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    user_id, workspace_id = scope(account)
    await result(service.owner(database, user_id, workspace_id, lock=True))
    row = await database.scalar(
        select(NoaSettings).where(
            NoaSettings.user_id == user_id, NoaSettings.workspace_id == workspace_id
        )
    )
    if payload.revision != (row.revision if row else 0):
        raise HTTPException(409, "Your Noa choice changed. Reload it and try again")
    if row is None:
        row = NoaSettings(user_id=user_id, workspace_id=workspace_id, revision=0)
        database.add(row)
    row.look, row.vibe = payload.look, payload.vibe
    row.revision += 1
    await database.commit()
    return {"look": row.look, "vibe": row.vibe, "revision": row.revision}
