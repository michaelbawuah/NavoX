"""Resolve current authorization and minimize registered sources before any model call."""

import json
import re
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from pydantic import Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.foundation.contracts import (
    AITask,
    ContextReference,
    Contract,
    JSONDocument,
    Sensitivity,
)
from navox.ai.gateway import minimized_source_payload
from navox.connectors.authorization import owned_connector
from navox.connectors.builtin.google import GOOGLE_CONNECTOR_KEY
from navox.connectors.contracts import CanonicalResource
from navox.connectors.generic_registration import approved_generic_connectors
from navox.connectors.mcp_registration import approved_mcp_policies
from navox.connectors.normalization import canonical_resource_to_source_document
from navox.connectors.sync_state import utc
from navox.core.settings import Settings
from navox.db.models import (
    Connection,
    ConnectorDefinition,
    ConnectorResource,
    User,
    WorkspaceMembership,
)
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import source_document_hash
from navox.providers.google_sources import SOURCE_SCOPES


class ContextDenied(ValueError):
    pass


def context_capabilities(settings: Settings | None = None) -> dict[tuple[str, str], frozenset[str]]:
    """Resource-to-read grants come from shipped code or operator-approved configuration."""
    mappings = {
        (GOOGLE_CONNECTOR_KEY, "communication.message"): {"communication.messages.read"},
        (GOOGLE_CONNECTOR_KEY, "calendar.event"): {"calendar.events.read"},
        ("google-gmail", "communication.message"): {"communication.messages.read"},
        ("google-calendar", "calendar.event"): {"calendar.events.read"},
        ("canvas-lms", "academic.assignment"): {"academic.assignments.read"},
        ("canvas-lms", "academic.announcement"): {"academic.announcements.read"},
        ("canvas-lms", "academic.course"): {"academic.courses.read"},
        ("canvas-lms", "calendar.event"): {"calendar.events.read"},
        ("generic-import", "import.calendar_event"): {"imports.calendar.read"},
        ("generic-import", "import.csv_row"): {"imports.tabular.read"},
        ("generic-import", "import.json_item"): {"imports.json.read"},
    }
    if settings is not None:
        for approved in approved_generic_connectors(settings):
            for endpoint in approved.config.endpoints:
                mappings[(approved.connector_key, endpoint.resource_type)] = {endpoint.capability}
        for policy in approved_mcp_policies(settings):
            grants = [(r.resource_type, r.capability) for r in policy.resources] + [
                (t.resource_type, t.capability) for t in policy.read_tools
            ]
            for resource_type, capability in grants:
                # Ambiguous types require all associated reads, never a grant named
                # by untrusted resource metadata or model output.
                mappings.setdefault((policy.connector_key, resource_type), set()).add(capability)
    return {key: frozenset(value) for key, value in mappings.items()}


_CREDENTIAL = re.compile(
    r"(?:\b(?:sk|rk)_(?:test|live)_[A-Za-z0-9]{12,}|\bsk-[A-Za-z0-9_-]{16,}|"
    r"\bAIza[A-Za-z0-9_-]{25,}|\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{16,}|"
    r"\bya29\.[A-Za-z0-9_.-]{15,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret)"
    r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_.-]{12,}|\bBearer\s+[A-Za-z0-9_.-]{16,})",
    re.IGNORECASE,
)


def reject_credentials(content: str, secrets: tuple[SecretStr, ...] = ()) -> None:
    if _CREDENTIAL.search(content) or any(
        value.get_secret_value() and value.get_secret_value() in content for value in secrets
    ):
        raise ContextDenied("Context contains credential-like data")


class MinimizedContext(Contract):
    workspace_id: UUID
    user_id: UUID
    source_ids: tuple[UUID, ...]
    sensitivity: Sensitivity
    content: JSONDocument = Field(repr=False)


class AuthorizedContext(Protocol):
    async def build(
        self,
        task: AITask,
        documents: Mapping[UUID, SourceDocument],
        *,
        user_request: str = "",
    ) -> MinimizedContext: ...


class ContextBuilder:
    def __init__(
        self,
        database: AsyncSession,
        *,
        secrets: tuple[SecretStr, ...] = (),
        fetched_sources: tuple[CanonicalResource, ...] = (),
        capability_map: Mapping[tuple[str, str], frozenset[str]] | None = None,
    ) -> None:
        self.database = database
        self.secrets = secrets
        self.capability_map = dict(
            context_capabilities() if capability_map is None else capability_map
        )
        # Only server connector call sites may supply freshly fetched resources.
        # This avoids persisting message bodies before a fenced sync consumer runs.
        # These are never accepted from HTTP clients, model output or source metadata.
        self.fetched_sources = {r.resource_id: r.model_copy(deep=True) for r in fetched_sources}

    async def authorize_resource(
        self, task: AITask, reference: ContextReference
    ) -> tuple[ConnectorResource, str | None, UUID]:
        """Authorize source metadata; this does not authorize an arbitrary source body."""
        user = await self.database.get(User, task.user_id, populate_existing=True)
        member = await self.database.get(
            WorkspaceMembership, (task.workspace_id, task.user_id), populate_existing=True
        )
        if (
            user is None
            or user.agent_paused
            or member is None
            or (reference.workspace_id, reference.user_id) != (task.workspace_id, task.user_id)
        ):
            raise ContextDenied("Context access denied")
        resource = await self.database.scalar(
            select(ConnectorResource)
            .where(
                ConnectorResource.id == reference.source_id,
            )
            .execution_options(populate_existing=True)
        )
        fetched = self.fetched_sources.get(reference.source_id)
        if resource is not None and (
            resource.workspace_id != task.workspace_id
            or resource.connector_connection_id != reference.connection_id
            or resource.deleted
        ):
            raise ContextDenied("Registered context source is unavailable")
        if fetched is not None:
            if (
                fetched.workspace_id != task.workspace_id
                or fetched.connector_connection_id != reference.connection_id
                or fetched.canonical.get("status") == "deleted"
            ):
                raise ContextDenied("Fetched context source binding failed")
            if resource is not None and (
                resource.provider != fetched.provider
                or resource.resource_type != fetched.resource_type
                or resource.external_id != fetched.external_id
                or utc(resource.retrieved_at) > utc(fetched.retrieved_at)
                or (
                    resource.source_updated_at is not None
                    and (
                        fetched.updated_at is None
                        or utc(resource.source_updated_at) > utc(fetched.updated_at)
                    )
                )
            ):
                raise ContextDenied("Fetched source was superseded")
            resource = ConnectorResource(
                id=fetched.resource_id,
                workspace_id=fetched.workspace_id,
                connector_connection_id=fetched.connector_connection_id,
                provider=fetched.provider,
                resource_type=fetched.resource_type,
                external_id=fetched.external_id,
                external_parent_id=fetched.external_parent_id,
                canonical=fetched.canonical,
                provider_metadata=fetched.provider_metadata,
                source_url=fetched.source_url,
                source_created_at=fetched.created_at,
                source_updated_at=fetched.updated_at,
                retrieved_at=fetched.retrieved_at,
                deleted=False,
            )
        if resource is None:
            raise ContextDenied("Registered context source is unavailable")
        connector = await owned_connector(
            self.database,
            connection_id=reference.connection_id,
            workspace_id=task.workspace_id,
            user_id=task.user_id,
            require_active=True,
            lock_connection=False,
        )
        if connector.provider != resource.provider:
            raise ContextDenied("Source provider binding failed")
        definition = await self.database.get(
            ConnectorDefinition, connector.connector_definition_id, populate_existing=True
        )
        required = self.capability_map.get(
            (definition.connector_key, resource.resource_type) if definition else ("", "")
        )
        if (
            not required
            or not required.issubset(connector.authorized_capabilities)
            or not required.issubset(connector.provider_capabilities)
        ):
            raise ContextDenied("Source read capability is unavailable")
        owner_email: str | None = user.email
        if connector.legacy_connection_id:
            legacy = await self.database.get(
                Connection, connector.legacy_connection_id, populate_existing=True
            )
            if (
                legacy is None
                or legacy.status != "active"
                or (legacy.workspace_id, legacy.user_id) != (task.workspace_id, task.user_id)
            ):
                raise ContextDenied("Source connection is unavailable")
            if legacy.provider == "google":
                owner_email = legacy.external_email
                source = (
                    "gmail" if resource.resource_type == "communication.message" else "calendar"
                )
                if SOURCE_SCOPES[source] not in legacy.granted_scopes:
                    raise ContextDenied("Source read capability was revoked")
        # The connector config is server-managed; source metadata cannot lower
        # classification. Unclassified imported personal data has a PERSONAL floor.
        classification = connector.config.get("ai_sensitivity", "PERSONAL")
        if not isinstance(classification, str):
            raise ContextDenied("Source classification is invalid")
        sensitivity = Sensitivity(classification)
        if sensitivity.rank < Sensitivity.PERSONAL.rank:
            sensitivity = Sensitivity.PERSONAL
        if reference.sensitivity != sensitivity or task.sensitivity.rank < sensitivity.rank:
            raise ContextDenied("Source classification changed or was downgraded")
        if resource.canonical.get("status") == "deleted":
            raise ContextDenied("Context source is deleted")
        return resource, owner_email, connector.legacy_connection_id or connector.id

    async def build(
        self,
        task: AITask,
        documents: Mapping[UUID, SourceDocument],
        *,
        user_request: str = "",
    ) -> MinimizedContext:
        # Documents are fetched by server-side connectors. Binding checks below
        # reject even a legitimate document supplied under another source's ID.
        user = await self.database.get(User, task.user_id, populate_existing=True)
        member = await self.database.get(
            WorkspaceMembership, (task.workspace_id, task.user_id), populate_existing=True
        )
        if user is None or user.agent_paused or member is None:
            raise ContextDenied("Context access denied")
        if len(user_request) > 64_000 or set(documents) != {
            r.source_id for r in task.context_references
        }:
            raise ContextDenied("Context selection does not match the task")
        sources: list[dict[str, object]] = []
        for reference in task.context_references:
            resource, owner_email, provenance_id = await self.authorize_resource(task, reference)
            document = SourceDocument.model_validate(documents[reference.source_id])
            if (document.workspace_id, document.provider, document.external_id) != (
                resource.workspace_id,
                resource.provider,
                resource.external_id,
            ):
                raise ContextDenied("Context source binding failed")
            if document.metadata.get("status") == "deleted":
                raise ContextDenied("Context source is deleted")
            source_hash = resource.provider_metadata.get("source_hash")
            if source_hash is None and resource.provider != "google":
                stored = CanonicalResource.model_validate(
                    {
                        "resource_id": resource.id,
                        "workspace_id": resource.workspace_id,
                        "connector_connection_id": resource.connector_connection_id,
                        "provider": resource.provider,
                        "resource_type": resource.resource_type,
                        "external_id": resource.external_id,
                        "external_parent_id": resource.external_parent_id,
                        "canonical": resource.canonical,
                        "provider_metadata": resource.provider_metadata,
                        "source_url": resource.source_url,
                        "created_at": utc(resource.source_created_at)
                        if resource.source_created_at
                        else None,
                        "updated_at": utc(resource.source_updated_at)
                        if resource.source_updated_at
                        else None,
                        "retrieved_at": utc(resource.retrieved_at),
                    }
                )
                registered = canonical_resource_to_source_document(
                    stored, provenance_connection_id=provenance_id
                )
                source_hash = source_document_hash(registered)
            if not isinstance(source_hash, str) or source_document_hash(document) != source_hash:
                raise ContextDenied("Context revision must be registered before use")
            sources.append(
                {
                    "source_id": str(resource.id),
                    "document": json.loads(
                        minimized_source_payload(document, owner_email=owner_email)
                    ),
                }
            )
        text = json.dumps({"sources": sources, "user_request": user_request}, ensure_ascii=False)
        reject_credentials(text, self.secrets)
        return MinimizedContext(
            workspace_id=task.workspace_id,
            user_id=task.user_id,
            source_ids=tuple(r.source_id for r in task.context_references),
            sensitivity=task.sensitivity,
            content=JSONDocument(text=text),
        )
