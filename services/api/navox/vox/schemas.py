"""Bounded ownership leases; no audio, transcript, or provider credentials."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Generation = Annotated[int, Field(strict=True, ge=0, le=2147483647)]


class VoxStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_call_id: UUID
    generation: Generation
    mode: Literal["off-record"]


class VoxLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: Generation


class VoxCallView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    client_call_id: UUID
    generation: Generation
    status: Literal["reserved", "ended"]
    mode: Literal["off-record"]
    created_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    end_reason: Literal["hang-up", "sign-out", "expired"] | None
    media_enabled: Literal[False] = False


class VoxContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: Generation
    call: VoxCallView | None
    media_enabled: Literal[False] = False
