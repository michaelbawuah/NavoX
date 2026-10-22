"""Private media admission data; never accepts provider locators or grants."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from navox.vox.schemas import Generation


class MediaStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: Generation
    request_id: UUID


class MediaBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    client_call_id: UUID
    generation: Generation
    room_name: str
    participant_identity: str
    agent_identity: str
    expires_at: datetime


class MediaGrant(MediaBinding):
    server_url: str
    token: str = Field(repr=False)
