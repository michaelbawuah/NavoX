"""First-party connectors and compatibility bridges."""

from navox.connectors.builtin.google import (
    GOOGLE_MANIFEST,
    ensure_google_connector_connection,
    google_canonical_resource,
    mirror_google_document,
)

__all__ = [
    "GOOGLE_MANIFEST",
    "ensure_google_connector_connection",
    "google_canonical_resource",
    "mirror_google_document",
]
