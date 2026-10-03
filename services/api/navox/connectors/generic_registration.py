"""Deployment-reviewed REST configurations; no user-supplied origin reaches the runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from navox.connectors.builtin.generic_api import GenericAPIConfig, GenericAPIConnector
from navox.connectors.contracts import ConnectorManifest
from navox.core.settings import Settings


class ApprovedGenericConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,25}[a-z0-9]$", max_length=27)
    config: GenericAPIConfig
    usage: Literal["OPERATIONAL", "NEWS"] = "OPERATIONAL"

    @model_validator(mode="after")
    def reject_reserved_provider(self) -> ApprovedGenericConfig:
        if self.config.provider in {"google", "canvas", "import"}:
            raise ValueError("Configured REST provider name is reserved")
        if self.usage == "NEWS" and (
            self.config.provider != "perigon"
            or self.config.base_url != "https://api.perigon.io"
            or self.config.auth != "bearer"
            or self.config.token_secret_name != "API_TOKEN"
            or any(
                endpoint.path != "/v1/articles/all"
                or not endpoint.capability.startswith("news.")
                or not endpoint.resource_type.startswith("news.")
                or endpoint.content_field is not None
                or endpoint.items_field != "articles"
                or endpoint.id_field != "articleId"
                or endpoint.subject_field != "title"
                or endpoint.occurred_at_field != "pubDate"
                or endpoint.source_url_field != "url"
                or endpoint.cursor_param is not None
                or endpoint.page_size_param != "size"
                or endpoint.page_size > 25
                for endpoint in self.config.endpoints
            )
        ):
            raise ValueError("News REST credentials require the bounded Perigon metadata profile")
        return self

    @property
    def connector_key(self) -> str:
        # Any operator-side config change creates a different registry entry.
        # Old connections fail closed until their owner reconnects to the new approval.
        digest = hashlib.sha256(self._canonical_config()).hexdigest()[:16]
        return f"generic-rest-{self.id}-{digest}"

    def _canonical_config(self) -> bytes:
        payload = self.config.model_dump(mode="json")
        # Existing operational approvals keep their exact identity.
        if self.usage != "OPERATIONAL":
            payload["usage"] = self.usage
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @property
    def manifest(self) -> ConnectorManifest:
        generic = GenericAPIConnector(self.config.model_dump(mode="json"), None)
        return ConnectorManifest.model_validate(
            {
                **generic.get_manifest().model_dump(mode="json", by_alias=True),
                "id": self.connector_key,
            }
        )


def approved_generic_connectors(settings: Settings) -> tuple[ApprovedGenericConfig, ...]:
    if len(settings.generic_rest_connectors) > 20:
        raise ValueError("Too many operator-approved REST connectors")
    result = tuple(
        ApprovedGenericConfig.model_validate(item) for item in settings.generic_rest_connectors
    )
    if len({item.id for item in result}) != len(result):
        raise ValueError("Duplicate operator-approved REST connector identifier")
    return result
