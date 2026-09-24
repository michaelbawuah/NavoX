from __future__ import annotations

from collections.abc import Mapping

from pydantic import JsonValue

from navox.connectors.builtin.google import GoogleCompatibilityConnector
from navox.connectors.builtin.imports import IMPORT_MANIFEST
from navox.connectors.builtin.oauth_canvas import OAUTH_CANVAS_MANIFEST, OAuthCanvasConnector
from navox.connectors.builtin.stored_import import StoredImportConnector
from navox.connectors.contracts import NavoXConnector, SecretAccessor
from navox.connectors.import_storage import ImportSnapshotReader
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
    return registry
