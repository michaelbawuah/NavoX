"""Minimized saved-state context with complete source authorization on every read."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.context import (
    ContextBuilder,
    ContextDenied,
    MinimizedContext,
    context_capabilities,
    reject_credentials,
)
from navox.ai.domains import (
    DOMAIN_TASKS,
    Domain,
    DomainInput,
    OperationalItem,
    domain_prompt_reference,
    domain_reference,
)
from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    ContextReference,
    JSONDocument,
    LatencyClass,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
)
from navox.ai.foundation.persistence import digest
from navox.core.settings import Settings
from navox.db.models import (
    Commitment,
    CommitmentSource,
    ConnectorConnection,
    ConnectorResource,
    User,
    WorkspaceMembership,
)
from navox.intelligence.contracts import SourceDocument


def operational_task(
    domain: Domain,
    *,
    workspace_id: UUID,
    user_id: UUID,
    sensitivity: Sensitivity,
    policy: ProviderPolicy,
    max_cost: object = "0.25",
) -> AITask:
    profile, task_type = DOMAIN_TASKS[domain]
    return AITask.model_validate(
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "profile": profile,
            "task_type": task_type,
            "capability_requirements": {Capability.TEXT, Capability.STRUCTURED_OUTPUT},
            "output_schema": domain_reference(domain),
            "prompt": domain_prompt_reference(domain),
            "sensitivity": sensitivity,
            "latency_class": LatencyClass.INTERACTIVE,
            "quality_class": QualityClass.HIGH,
            "max_cost": max_cost,
            "max_output_tokens": 2000,
            "provider_policy": policy,
        }
    )


@dataclass(frozen=True)
class OperationalSnapshot:
    context: DomainInput
    sensitivity: Sensitivity
    fingerprint: str


class OperationalContext:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        workspace_id: UUID,
        user_id: UUID,
        domain: Domain,
        item_ids: tuple[UUID, ...],
        instructions: str,
        target_id: UUID | None = None,
    ) -> None:
        if not 1 <= len(item_ids) <= 12 or len(set(item_ids)) != len(item_ids):
            raise ContextDenied("Select between one and twelve distinct saved items")
        if target_id is not None and target_id not in item_ids:
            raise ContextDenied("Target must be one of the selected saved items")
        self.factory, self.settings = factory, settings
        self.workspace_id, self.user_id, self.domain = workspace_id, user_id, domain
        self.item_ids, self.instructions, self.target_id = item_ids, instructions, target_id
        self.as_of = datetime.now(UTC)
        self.initial: OperationalSnapshot | None = None

    async def prepare(self) -> OperationalSnapshot:
        self.initial = await self._read()
        return self.initial

    async def _read(self) -> OperationalSnapshot:
        # A new short session sees revocation/edits made during a provider call and
        # never holds read locks while waiting for the provider or trace writer.
        async with self.factory() as db:
            user = await db.get(User, self.user_id)
            member = await db.get(WorkspaceMembership, (self.workspace_id, self.user_id))
            if user is None or user.agent_paused or member is None:
                raise ContextDenied("Saved-state access is unavailable")
            items = []
            references: dict[UUID, ContextReference] = {}
            provenance: list[list[object]] = []
            sensitivity = Sensitivity.PERSONAL
            for identifier in self.item_ids:
                row = await db.scalar(
                    select(Commitment).where(
                        Commitment.id == identifier,
                        Commitment.workspace_id == self.workspace_id,
                        Commitment.user_id == self.user_id,
                    )
                )
                if row is None:
                    raise ContextDenied("Selected saved item is unavailable")
                items.append(
                    OperationalItem(
                        id=row.id,
                        kind=row.commitment_type,
                        title=row.title,
                        description=row.description[:2048] if row.description else None,
                        status=row.status,
                        priority=row.priority,
                        due_at=row.due_at.replace(tzinfo=UTC)
                        if row.due_at is not None and row.due_at.tzinfo is None
                        else row.due_at,
                    )
                )
                sources = list(
                    await db.scalars(
                        select(CommitmentSource)
                        .where(
                            CommitmentSource.commitment_id == row.id,
                        )
                        .order_by(CommitmentSource.id)
                        .limit(33)
                    )
                )
                if len(sources) > 32 or (not sources and row.created_by != "user"):
                    raise ContextDenied("Saved item provenance is incomplete or too large")
                provenance.append([str(row.id), str(row.updated_at), row.created_by])
                for source in sources:
                    if (source.provider, source.source_type) == ("navox", "manual"):
                        if row.created_by != "user" or source.connection_id is not None:
                            raise ContextDenied("Manual source provenance is invalid")
                        provenance.append([str(source.id), "manual"])
                        continue
                    if source.connection_id is None or not source.external_resource_id:
                        raise ContextDenied("Saved source attribution is unavailable")
                    connector = await db.scalar(
                        select(ConnectorConnection).where(
                            ConnectorConnection.legacy_connection_id == source.connection_id,
                            ConnectorConnection.workspace_id == self.workspace_id,
                            ConnectorConnection.user_id == self.user_id,
                            ConnectorConnection.provider == source.provider,
                        )
                    )
                    if connector is None:
                        raise ContextDenied("Saved source has no registered connection")
                    classification = connector.config.get("ai_sensitivity", "PERSONAL")
                    if not isinstance(classification, str):
                        raise ContextDenied("Source classification is invalid")
                    try:
                        classified = Sensitivity(classification)
                    except ValueError:
                        raise ContextDenied("Source classification is invalid") from None
                    classified = max((Sensitivity.PERSONAL, classified), key=lambda s: s.rank)
                    sensitivity = max((sensitivity, classified), key=lambda s: s.rank)
                    resources = list(
                        await db.scalars(
                            select(ConnectorResource)
                            .where(
                                ConnectorResource.connector_connection_id == connector.id,
                                ConnectorResource.workspace_id == self.workspace_id,
                                ConnectorResource.provider == source.provider,
                                ConnectorResource.external_id == source.external_resource_id,
                            )
                            .limit(33)
                        )
                    )
                    matches = []
                    for resource in resources:
                        envelope = resource.canonical.get("source_document")
                        source_type = (
                            envelope.get("source_type")
                            if isinstance(envelope, dict)
                            else resource.canonical.get("source_type", resource.resource_type)
                        )
                        if source_type == source.source_type:
                            matches.append(resource)
                    if len(resources) > 32 or len(matches) != 1 or matches[0].deleted:
                        raise ContextDenied("Saved source is missing, deleted or ambiguous")
                    resource = matches[0]
                    references[resource.id] = ContextReference(
                        workspace_id=self.workspace_id,
                        user_id=self.user_id,
                        source_id=resource.id,
                        connection_id=connector.id,
                        sensitivity=classified,
                    )
                    provenance.append(
                        [
                            str(source.id),
                            str(resource.id),
                            str(resource.source_updated_at),
                            str(resource.retrieved_at),
                            source.source_metadata,
                            classified.value,
                            resource.canonical,
                            resource.provider_metadata,
                        ]
                    )
                    if len(references) > 32:
                        raise ContextDenied("Selected provenance exceeds the context limit")
            context = DomainInput(
                items=tuple(items),
                instructions=self.instructions,
                as_of=self.as_of,
                target_id=self.target_id,
            )
            from navox.ai.features import configured_secrets

            secrets = configured_secrets(self.settings)
            task = operational_task(
                self.domain,
                workspace_id=self.workspace_id,
                user_id=self.user_id,
                sensitivity=sensitivity,
                policy=ProviderPolicy(
                    workspace_id=self.workspace_id, user_id=self.user_id, revision=1
                ),
            )
            source_task = AITask.model_validate(
                {**task.model_dump(), "context_references": tuple(references.values())}
            )
            # Saved-state assistance needs current source authority, not source
            # bodies. Google deliberately retains only source hashes. Reuse the
            # same authorization boundary without fetching or rehydrating mail.
            source_authority = ContextBuilder(
                db, secrets=secrets, capability_map=context_capabilities(self.settings)
            )
            for reference in references.values():
                await source_authority.authorize_resource(source_task, reference)
            reject_credentials(context.model_dump_json(), secrets)
            fingerprint = digest(
                json.dumps(
                    {"context": context.model_dump(mode="json"), "provenance": provenance},
                    sort_keys=True,
                )
            )
            return OperationalSnapshot(context, sensitivity, fingerprint)

    async def build(
        self,
        task: AITask,
        documents: Mapping[UUID, SourceDocument],
        *,
        user_request: str = "",
    ) -> MinimizedContext:
        if (
            (task.workspace_id, task.user_id) != (self.workspace_id, self.user_id)
            or task.context_references
            or documents
            or user_request
        ):
            raise ContextDenied("Operational context scope mismatch")
        current = await self._read()
        if (
            self.initial is None
            or current != self.initial
            or task.sensitivity != current.sensitivity
        ):
            raise ContextDenied("Selected saved state changed; refresh before asking again")
        return MinimizedContext(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            source_ids=self.item_ids,
            sensitivity=current.sensitivity,
            content=JSONDocument(
                text=json.dumps({"operational_context": current.context.model_dump(mode="json")})
            ),
        )
