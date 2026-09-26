"""Feature adapters request logical profiles; only the registry chooses model IDs."""

import json
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.configured import build_runtime
from navox.ai.context import ContextBuilder, ContextDenied, context_capabilities
from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    ContextReference,
    LatencyClass,
    Profile,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
    VersionedRef,
)
from navox.ai.routing import PolicyRules
from navox.connectors.builtin.google import google_canonical_resource
from navox.connectors.contracts import CanonicalResource
from navox.core.settings import Settings
from navox.db.models import ConnectorConnection, ConnectorResource
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import ModelExtractionResponse, OperationalExtraction


def configured_secrets(settings: Settings) -> tuple[SecretStr, ...]:
    return tuple(value for value in settings.__dict__.values() if isinstance(value, SecretStr))


def source_task(
    *,
    workspace_id: UUID,
    user_id: UUID,
    connector: ConnectorConnection,
    source_id: UUID,
    policy: PolicyRules,
    profile: Profile,
    task_type: TaskType,
    prompt: str,
    latency: LatencyClass = LatencyClass.BACKGROUND,
    max_output_tokens: int = 4000,
) -> AITask:
    classification = connector.config.get("ai_sensitivity", "PERSONAL")
    if not isinstance(classification, str):
        raise ContextDenied("Source classification is invalid")
    sensitivity = Sensitivity(classification)
    if sensitivity.rank < Sensitivity.PERSONAL.rank:
        sensitivity = Sensitivity.PERSONAL
    return AITask(
        workspace_id=workspace_id,
        user_id=user_id,
        task_type=task_type,
        profile=profile,
        capability_requirements=frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
        context_references=(
            ContextReference(
                workspace_id=workspace_id,
                user_id=user_id,
                source_id=source_id,
                connection_id=connector.id,
                sensitivity=sensitivity,
            ),
        ),
        output_schema=VersionedRef(name=prompt, version="v1"),
        prompt=VersionedRef(name=prompt, version="v1"),
        sensitivity=sensitivity,
        latency_class=latency,
        quality_class=QualityClass.HIGH,
        max_cost=policy.max_cost,
        max_output_tokens=max_output_tokens,
        provider_policy=ProviderPolicy(
            workspace_id=workspace_id,
            user_id=user_id,
            revision=1,
            grants=policy.grants,
            allow_fallback=policy.allow_fallback,
            max_fallbacks=policy.max_fallbacks if policy.allow_fallback else 0,
        ),
    )


class RegisteredExtractionGateway:
    def __init__(
        self,
        settings: Settings,
        database: AsyncSession,
        *,
        workspace_id: UUID,
        user_id: UUID,
        connection_id: UUID,
        fetched_source: CanonicalResource | None = None,
    ) -> None:
        self.settings, self.database = settings, database
        self.workspace_id, self.user_id, self.connection_id = workspace_id, user_id, connection_id
        self.fetched_source = fetched_source

    async def extract_operational(
        self, document: SourceDocument, *, owner_email: str | None = None
    ) -> ModelExtractionResponse:
        if document.workspace_id != self.workspace_id:
            raise ContextDenied("Source workspace does not match feature scope")
        connector = await self.database.scalar(
            select(ConnectorConnection).where(
                (ConnectorConnection.id == self.connection_id)
                | (ConnectorConnection.legacy_connection_id == self.connection_id),
                ConnectorConnection.workspace_id == self.workspace_id,
                ConnectorConnection.user_id == self.user_id,
            )
        )
        if connector is None:
            raise ContextDenied("Registered source connection is unavailable")
        fetched = self.fetched_source
        if document.provider == "google":
            fetched = google_canonical_resource(document, connector_connection_id=connector.id)
        if fetched is not None:
            source_id = fetched.resource_id
        else:
            stored = await self.database.scalar(
                select(ConnectorResource).where(
                    ConnectorResource.connector_connection_id == connector.id,
                    ConnectorResource.workspace_id == self.workspace_id,
                    ConnectorResource.provider == document.provider,
                    ConnectorResource.external_id == document.external_id,
                    ConnectorResource.deleted.is_(False),
                )
            )
            if stored is None:
                raise ContextDenied("Registered source is unavailable")
            source_id = stored.id
        runtime = await build_runtime(self.settings)
        task = source_task(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            connector=connector,
            source_id=source_id,
            policy=runtime.store.operator_policy,
            profile=Profile.EXTRACTION_HIGH_ACCURACY,
            task_type=TaskType.EXTRACT,
            prompt="commitment_extraction",
        )

        def validate(value: Any) -> None:
            extraction = OperationalExtraction.model_validate(value).reanchor_unique_evidence(
                document
            )
            extraction.validate_evidence(document)
            if document.source_type == "gmail_message" and any(
                item.email_relevance is None for item in extraction.observations
            ):
                raise ValueError("Gmail relevance is missing")

        result = await runtime.execute(
            task,
            context_builder=ContextBuilder(
                self.database,
                secrets=configured_secrets(self.settings),
                fetched_sources=(fetched,) if fetched is not None else (),
                capability_map=context_capabilities(self.settings),
            ),
            documents={source_id: document},
            semantic_validator=validate,
        )
        return ModelExtractionResponse(
            output=json.loads(result.output.text),
            provider=result.provider.value,
            model=result.model,
        )
