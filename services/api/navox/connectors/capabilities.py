from __future__ import annotations

from dataclasses import dataclass

from navox.connectors.contracts import ConnectorHealthState, ConnectorManifest


@dataclass(frozen=True)
class EffectiveCapabilities:
    read: frozenset[str]
    write: frozenset[str]
    events: frozenset[str]

    @property
    def all(self) -> frozenset[str]:
        return self.read | self.write | self.events


class CapabilityGateway:
    """Deterministic capability intersection; declaration never grants permission."""

    def evaluate(
        self,
        *,
        manifest: ConnectorManifest,
        provider_capabilities: set[str] | frozenset[str],
        user_authorized: set[str] | frozenset[str],
        policy_allowed: set[str] | frozenset[str],
        health_state: ConnectorHealthState,
    ) -> EffectiveCapabilities:
        connector_read = {item.name for item in manifest.capabilities.read}
        connector_write = {item.name for item in manifest.capabilities.write}
        connector_events = set(manifest.capabilities.events)
        allowed = set(provider_capabilities) & set(user_authorized) & set(policy_allowed)

        if health_state in {
            "AUTH_EXPIRED",
            "RATE_LIMITED",
            "SYNC_FAILED",
            "PAUSED",
            "DISCONNECTED",
        }:
            return EffectiveCapabilities(frozenset(), frozenset(), frozenset())

        read = connector_read & allowed
        events = connector_events & allowed
        write = connector_write & allowed if health_state == "CONNECTED" else set()
        return EffectiveCapabilities(
            read=frozenset(read),
            write=frozenset(write),
            events=frozenset(events),
        )
