"""Operator-reviewed multi-institution Canvas configuration boundaries."""

import pytest

from navox.connectors.canvas_oauth import (
    deployment_for_connection,
    deployment_for_fingerprint,
    deployments,
)
from navox.connectors.contracts import ConnectorRuntimeError
from navox.core.settings import Settings


def configured(entries):
    return Settings(_env_file=None, canvas_oauth_deployments=entries)


def schools():
    return [
        {
            "id": "north",
            "name": "North University",
            "origin": "https://canvas.north.edu",
            "client_id": "123",
            "client_secret": "north-secret",
        },
        {
            "id": "south",
            "name": "South College",
            "origin": "https://canvas.south.edu",
            "client_id": "456",
            "client_secret": "south-secret",
        },
    ]


def test_two_schools_resolve_only_their_own_fingerprint_and_origin():
    settings = configured(schools())
    north, south = deployments(settings)
    assert north.fingerprint != south.fingerprint
    assert deployment_for_fingerprint(settings, south.fingerprint) == south
    assert deployment_for_connection(settings, north.origin, north.fingerprint) == north
    with pytest.raises(ConnectorRuntimeError):
        deployment_for_connection(settings, south.origin, north.fingerprint)
    with pytest.raises(ConnectorRuntimeError):
        deployment_for_fingerprint(settings, "0" * 64)


@pytest.mark.parametrize(
    "change",
    [
        lambda entries: entries[1].update(id="north"),
        lambda entries: entries[1].update(origin="https://canvas.north.edu"),
        lambda entries: entries[1].update(origin="http://canvas.south.edu"),
        lambda entries: entries[1].update(origin="https://canvas.south.edu/redirect"),
        lambda entries: entries[1].update(origin="https://user:pass@canvas.south.edu"),
        lambda entries: entries[1].update(client_secret=""),
        lambda entries: entries[1].update(client_id="not-numeric"),
        lambda entries: entries[1].update(id="Wrong ID"),
        lambda entries: entries[1].update(extra="unsafe"),
    ],
)
def test_invalid_catalog_fails_closed(change):
    entries = schools()
    change(entries)
    with pytest.raises(ConnectorRuntimeError):
        deployments(configured(entries))


def test_rotated_or_rebound_school_fails_existing_connection():
    entries = schools()
    settings = configured(entries)
    north = deployments(settings)[0]
    entries[0]["client_secret"] = "rotated"
    with pytest.raises(ConnectorRuntimeError):
        deployment_for_connection(configured(entries), north.origin, north.fingerprint)
    entries[0]["client_secret"] = "north-secret"
    entries[0]["id"] = "replacement"
    with pytest.raises(ConnectorRuntimeError):
        deployment_for_connection(configured(entries), north.origin, north.fingerprint)
