"""Deployment-reviewed REST configurations; no user-supplied origin reaches the runtime."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from navox.connectors.builtin.generic_api import GenericAPIConfig, GenericAPIConnector
from navox.connectors.contracts import ConnectorManifest
from navox.core.settings import Settings


class ApprovedGenericConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,25}[a-z0-9]$", max_length=27)
    config: GenericAPIConfig

    @model_validator(mode="after")
    def reject_reserved_provider(self) -> ApprovedGenericConfig:
        if self.config.provider in {"google", "canvas", "import"}:
            raise ValueError("Configured REST provider name is reserved")
        return self

    @property
    def connector_key(self) -> str:
        # Any operator-side config change creates a different registry entry.
        # Old connections fail closed until their owner reconnects to the new approval.
        digest = hashlib.sha256(self._canonical_config()).hexdigest()[:16]
        return f"generic-rest-{self.id}-{digest}"

    def _canonical_config(self) -> bytes:
        return json.dumps(
            self.config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

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
