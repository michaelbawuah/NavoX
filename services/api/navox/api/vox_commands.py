"""Owned wake-question queue; browser requests never report billing or grants."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.vox import (
    PrivateVoxRoute,
    SessionHashDependency,
    bounded_json,
    enabled,
    expected_account,
    result,
    web_session_hash,
)
from navox.vox.commands import enqueue_wake

router = APIRouter(
    prefix="/vox/calls",
    tags=["vox"],
    route_class=PrivateVoxRoute,
    dependencies=[Depends(web_session_hash), Depends(expected_account), Depends(enabled)],
)


class WakeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(ge=0, strict=True)
    request_id: UUID
    text: str = Field(min_length=1, max_length=4000, strict=True, repr=False)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Invalid wake question")
        return value.strip()


@router.post("/{client_call_id}/media/{connection_id}/wake")
async def wake_question(
    client_call_id: UUID,
    connection_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    session_token_hash: SessionHashDependency,
    settings: SettingsDependency,
) -> dict[str, str]:
    payload = await bounded_json(request, WakeInput, max_bytes=32_000)
    await result(
        enqueue_wake(
            database,
            account.user.id,
            account.workspace.id,
            session_token_hash,
            client_call_id,
            connection_id,
            generation=payload.generation,
            request_id=payload.request_id,
            text=payload.text,
            settings=settings,
        )
    )
    return {"request_id": str(payload.request_id), "status": "accepted", "delivery": "queued"}
