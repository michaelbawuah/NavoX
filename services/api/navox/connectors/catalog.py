from __future__ import annotations

from navox.connectors.builtin.google import GoogleCompatibilityConnector
from navox.connectors.registry import ConnectorRegistry
from navox.core.settings import Settings


def build_connector_registry(settings: Settings) -> ConnectorRegistry:
    """Build only NavoX-shipped/validated connector factories.

    Settings are accepted so later connector factories can receive bounded
    deployment configuration without dynamic imports or arbitrary code loading.
    """

    del settings
    registry = ConnectorRegistry()
    google = GoogleCompatibilityConnector({})
    registry.register(google.get_manifest(), GoogleCompatibilityConnector)
    return registry
