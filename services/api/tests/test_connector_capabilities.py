from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.contracts import ConnectorManifest


def manifest() -> ConnectorManifest:
    return ConnectorManifest.model_validate(
        {
            "id": "demo-service",
            "version": "1.0.0",
            "displayName": "Demo",
            "category": "demo",
            "connectorClass": "GENERIC_API",
            "auth": [{"kind": "none", "label": "No auth", "scopes": []}],
            "resourceTypes": ["demo.item"],
            "capabilities": {
                "read": [
                    {
                        "name": "demo.items.read",
                        "description": "Read items",
                        "sensitive": False,
                    }
                ],
                "write": [
                    {
                        "name": "demo.items.write",
                        "description": "Write items",
                        "sensitive": True,
                    }
                ],
                "events": ["demo.items.changed"],
                "incrementalSync": True,
            },
            "requiredSecrets": [],
            "rateLimitStrategy": "none",
            "minimumNavoxConnectorApiVersion": "1",
        }
    )


def test_capability_gateway_requires_every_authority_layer() -> None:
    result = CapabilityGateway().evaluate(
        manifest=manifest(),
        provider_capabilities={
            "demo.items.read",
            "demo.items.write",
            "demo.items.changed",
            "provider.extra",
        },
        user_authorized={"demo.items.read", "demo.items.changed"},
        policy_allowed={"demo.items.read", "demo.items.write", "demo.items.changed"},
        health_state="CONNECTED",
    )

    assert result.read == frozenset({"demo.items.read"})
    assert result.write == frozenset()
    assert result.events == frozenset({"demo.items.changed"})
    assert "provider.extra" not in result.all


def test_degraded_health_removes_write_and_unhealthy_removes_everything() -> None:
    gateway = CapabilityGateway()
    common = {
        "manifest": manifest(),
        "provider_capabilities": {"demo.items.read", "demo.items.write"},
        "user_authorized": {"demo.items.read", "demo.items.write"},
        "policy_allowed": {"demo.items.read", "demo.items.write"},
    }

    degraded = gateway.evaluate(**common, health_state="DEGRADED")
    assert degraded.read == frozenset({"demo.items.read"})
    assert degraded.write == frozenset()

    paused = gateway.evaluate(**common, health_state="PAUSED")
    assert paused.all == frozenset()
