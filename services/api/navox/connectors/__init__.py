"""SPEC-003 universal connector platform."""

from navox.connectors.capabilities import CapabilityGateway, EffectiveCapabilities
from navox.connectors.contracts import (
    NAVOX_CONNECTOR_API_VERSION,
    CanonicalResource,
    ConnectorCapabilities,
    ConnectorConnectionContext,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    NavoXConnector,
    stable_resource_id,
)
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
from navox.connectors.secrets import ScopedSecretLease, SecretBroker, SecretBrokerError

__all__ = [
    "NAVOX_CONNECTOR_API_VERSION",
    "CanonicalResource",
    "CapabilityGateway",
    "ConnectorCapabilities",
    "ConnectorConnectionContext",
    "ConnectorHealth",
    "ConnectorManifest",
    "ConnectorRegistry",
    "ConnectorRuntime",
    "ConnectorRuntimeError",
    "EffectiveCapabilities",
    "NavoXConnector",
    "ScopedSecretLease",
    "SecretBroker",
    "SecretBrokerError",
    "stable_resource_id",
]
