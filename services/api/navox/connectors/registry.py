from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from navox.connectors.contracts import ConnectorManifest, NavoXConnector, SecretAccessor


class ConnectorFactory(Protocol):
    def __call__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
    ) -> NavoXConnector: ...


@dataclass(frozen=True)
class RegisteredConnector:
    manifest: ConnectorManifest
    factory: ConnectorFactory


class ConnectorRegistry:
    """Explicit connector registry. It never imports arbitrary third-party packages."""

    def __init__(self) -> None:
        self._connectors: dict[str, RegisteredConnector] = {}

    def register(self, manifest: ConnectorManifest, factory: ConnectorFactory) -> None:
        if manifest.id in self._connectors:
            raise ValueError(f"Connector already registered: {manifest.id}")
        self._connectors[manifest.id] = RegisteredConnector(manifest=manifest, factory=factory)

    def get(self, connector_id: str) -> RegisteredConnector:
        try:
            return self._connectors[connector_id]
        except KeyError:
            raise KeyError(f"Unknown connector: {connector_id}") from None

    def build(
        self,
        connector_id: str,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None = None,
    ) -> NavoXConnector:
        registered = self.get(connector_id)
        connector = registered.factory(config, secrets)
        actual = connector.get_manifest()
        if actual.id != registered.manifest.id or actual.version != registered.manifest.version:
            raise ValueError(
                "Connector factory returned a manifest that does not match registration"
            )
        return connector

    def manifests(self) -> tuple[ConnectorManifest, ...]:
        return tuple(
            item.manifest
            for item in sorted(self._connectors.values(), key=lambda item: item.manifest.id)
        )
