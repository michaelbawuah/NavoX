"""SPEC-003 universal connector platform."""

from importlib import import_module as _import_module
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any

# Lightweight contracts and parser children must not load the connector runtime.
_EXPORTS = {
    "NAVOX_CONNECTOR_API_VERSION": ("navox.connectors.contracts", "NAVOX_CONNECTOR_API_VERSION"),
    "CanonicalResource": ("navox.connectors.contracts", "CanonicalResource"),
    "CapabilityGateway": ("navox.connectors.capabilities", "CapabilityGateway"),
    "ConnectorCapabilities": ("navox.connectors.contracts", "ConnectorCapabilities"),
    "ConnectorConnectionContext": ("navox.connectors.contracts", "ConnectorConnectionContext"),
    "ConnectorHealth": ("navox.connectors.contracts", "ConnectorHealth"),
    "ConnectorManifest": ("navox.connectors.contracts", "ConnectorManifest"),
    "ConnectorRegistry": ("navox.connectors.registry", "ConnectorRegistry"),
    "ConnectorRuntime": ("navox.connectors.runtime", "ConnectorRuntime"),
    "ConnectorRuntimeError": ("navox.connectors.contracts", "ConnectorRuntimeError"),
    "EffectiveCapabilities": ("navox.connectors.capabilities", "EffectiveCapabilities"),
    "NavoXConnector": ("navox.connectors.contracts", "NavoXConnector"),
    "ScopedSecretLease": ("navox.connectors.secrets", "ScopedSecretLease"),
    "SecretBroker": ("navox.connectors.secrets", "SecretBroker"),
    "SecretBrokerError": ("navox.connectors.secrets", "SecretBrokerError"),
    "stable_resource_id": ("navox.connectors.contracts", "stable_resource_id"),
}

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

# Reload refreshes canonical exports rather than retaining cached old objects.
for _export in __all__:
    globals().pop(_export, None)

if _TYPE_CHECKING:
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


def __getattr__(name: str) -> _Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(_import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
