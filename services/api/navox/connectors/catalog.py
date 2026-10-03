from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from navox.connectors.builtin.generic_api import GenericAPIConnector
from navox.connectors.builtin.google import GoogleCompatibilityConnector
from navox.connectors.builtin.imports import IMPORT_MANIFEST
from navox.connectors.builtin.mcp import register_mcp_server
from navox.connectors.builtin.oauth_canvas import OAUTH_CANVAS_MANIFEST, OAuthCanvasConnector
from navox.connectors.builtin.stored_import import StoredImportConnector
from navox.connectors.contracts import ConnectorManifest, NavoXConnector, SecretAccessor
from navox.connectors.generic_registration import ApprovedGenericConfig, approved_generic_connectors
from navox.connectors.import_storage import ImportSnapshotReader
from navox.connectors.mcp_registration import approved_mcp_policies
from navox.connectors.registry import ConnectorRegistry
from navox.core.settings import Settings


def build_connector_registry(settings: Settings) -> ConnectorRegistry:
    """Build only NavoX-shipped/validated connector factories.

    Settings are accepted so later connector factories can receive bounded
    deployment configuration without dynamic imports or arbitrary code loading.
    """

    registry = ConnectorRegistry()
    google = GoogleCompatibilityConnector({}, None)
    registry.register(google.get_manifest(), GoogleCompatibilityConnector)

    def canvas(config: Mapping[str, JsonValue], secrets: SecretAccessor | None) -> NavoXConnector:
        return OAuthCanvasConnector(config, secrets, settings=settings)

    registry.register(OAUTH_CANVAS_MANIFEST, canvas)

    def imported(config: Mapping[str, JsonValue], secrets: SecretAccessor | None) -> NavoXConnector:
        return StoredImportConnector(config, secrets, reader=ImportSnapshotReader(settings))

    registry.register(IMPORT_MANIFEST, imported)
    if settings.stripe_sandbox_enabled and settings.app_environment.casefold() not in {
        "prod",
        "production",
    }:
        from navox.connectors.stripe_sandbox import MANIFEST, StripeSandboxConnector

        registry.register(MANIFEST, StripeSandboxConnector)
    for approved in approved_generic_connectors(settings):
        # News has its own rights, retention and source workflows. Do not route
        # these credentials through operational extraction or generic polling.
        if approved.usage == "NEWS":
            continue

        def generic(
            config: Mapping[str, JsonValue],
            secrets: SecretAccessor | None,
            *,
            selected: ApprovedGenericConfig = approved,
        ) -> NavoXConnector:
            if dict(config) != selected.config.model_dump(mode="json"):
                raise ValueError("Configured REST connector does not match operator approval")
            return ApprovedGenericAPIConnector(
                config, secrets, connector_key=selected.connector_key
            )

        registry.register(approved.manifest, generic)
    for policy in approved_mcp_policies(settings):
        register_mcp_server(registry, policy)
    return registry


class ApprovedGenericAPIConnector(GenericAPIConnector):
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
        *,
        connector_key: str,
    ) -> None:
        super().__init__(config, secrets)
        self.connector_key = connector_key

    def get_manifest(self) -> ConnectorManifest:
        return ConnectorManifest.model_validate(
            {
                **super().get_manifest().model_dump(mode="json", by_alias=True),
                "id": self.connector_key,
            }
        )
