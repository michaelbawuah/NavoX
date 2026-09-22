import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    Commitment,
    CommitmentRelation,
    CommitmentSource,
    Connection,
    Objective,
)


class ContextCommitment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    type: str
    title: str
    description: str | None
    status: str
    priority: int
    due_at: datetime | None
    created_by: str


class ContextObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    description: str | None
    priority: int
    target_at: datetime | None


class ContextSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    source_type: str
    external_resource_id: str | None
    extracted_at: datetime


class ContextRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_type: str
    direction: str
    commitment_id: UUID
    type: str
    title: str
    status: str


class ContextCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    status: str
    granted_scopes: list[str]


class AgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    user_id: UUID
    workspace_id: UUID
    commitment: ContextCommitment
    objective: ContextObjective | None
    sources: list[ContextSource]
    relations: list[ContextRelation]
    capabilities: list[ContextCapability]

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    def content_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode()
        return sha256(encoded).hexdigest()


class ContextNotFoundError(LookupError):
    pass


class ContextBuilder:
    """Build the smallest bounded context needed to handle one commitment."""

    max_sources = 12
    max_relations = 12

    async def build(
        self,
        database: AsyncSession,
        *,
        user_id: UUID,
        workspace_id: UUID,
        commitment_id: UUID,
    ) -> AgentContext:
        commitment = await database.scalar(
            select(Commitment).where(
                Commitment.id == commitment_id,
                Commitment.user_id == user_id,
                Commitment.workspace_id == workspace_id,
            )
        )
        if commitment is None:
            raise ContextNotFoundError("Commitment not found")

        objective = None
        if commitment.objective_id is not None:
            objective = await database.scalar(
                select(Objective).where(
                    Objective.id == commitment.objective_id,
                    Objective.user_id == user_id,
                    Objective.workspace_id == workspace_id,
                )
            )

        sources = list(
            await database.scalars(
                select(CommitmentSource)
                .where(CommitmentSource.commitment_id == commitment.id)
                .order_by(CommitmentSource.extracted_at)
                .limit(self.max_sources)
            )
        )

        relation_rows = list(
            await database.scalars(
                select(CommitmentRelation)
                .where(
                    or_(
                        CommitmentRelation.from_commitment_id == commitment.id,
                        CommitmentRelation.to_commitment_id == commitment.id,
                    )
                )
                .order_by(CommitmentRelation.created_at)
                .limit(self.max_relations)
            )
        )
        related_ids = {
            relation.to_commitment_id
            if relation.from_commitment_id == commitment.id
            else relation.from_commitment_id
            for relation in relation_rows
        }
        related = {}
        if related_ids:
            related = {
                item.id: item
                for item in await database.scalars(
                    select(Commitment).where(
                        Commitment.id.in_(related_ids),
                        Commitment.user_id == user_id,
                        Commitment.workspace_id == workspace_id,
                    )
                )
            }

        connections = list(
            await database.scalars(
                select(Connection)
                .where(
                    Connection.user_id == user_id,
                    Connection.workspace_id == workspace_id,
                )
                .order_by(Connection.provider, Connection.created_at)
            )
        )

        relations: list[ContextRelation] = []
        for relation in relation_rows:
            outgoing = relation.from_commitment_id == commitment.id
            related_id = relation.to_commitment_id if outgoing else relation.from_commitment_id
            related_commitment = related.get(related_id)
            if related_commitment is None:
                continue
            relations.append(
                ContextRelation(
                    relation_type=relation.relation_type,
                    direction="outgoing" if outgoing else "incoming",
                    commitment_id=related_commitment.id,
                    type=related_commitment.commitment_type,
                    title=related_commitment.title,
                    status=related_commitment.status,
                )
            )

        return AgentContext(
            generated_at=datetime.now(UTC),
            user_id=user_id,
            workspace_id=workspace_id,
            commitment=ContextCommitment(
                id=commitment.id,
                type=commitment.commitment_type,
                title=commitment.title,
                description=commitment.description,
                status=commitment.status,
                priority=commitment.priority,
                due_at=commitment.due_at,
                created_by=commitment.created_by,
            ),
            objective=(
                ContextObjective(
                    id=objective.id,
                    title=objective.title,
                    description=objective.description,
                    priority=objective.priority,
                    target_at=objective.target_at,
                )
                if objective is not None
                else None
            ),
            sources=[
                ContextSource(
                    provider=source.provider,
                    source_type=source.source_type,
                    external_resource_id=source.external_resource_id,
                    extracted_at=source.extracted_at,
                )
                for source in sources
            ],
            relations=relations,
            capabilities=[
                ContextCapability(
                    provider=connection.provider,
                    status=connection.status,
                    granted_scopes=sorted(connection.granted_scopes),
                )
                for connection in connections
            ],
        )
