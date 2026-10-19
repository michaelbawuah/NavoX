"""First-party connectors and compatibility bridges."""

from importlib import import_module as _import_module
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any

# Importing one bounded parser must not initialize unrelated provider adapters.
_EXPORTS = {
    "IMPORT_MANIFEST": ("navox.connectors.builtin.imports", "IMPORT_MANIFEST"),
    "ImportConnector": ("navox.connectors.builtin.imports", "ImportConnector"),
    "CANVAS_MANIFEST": ("navox.connectors.builtin.canvas", "CANVAS_MANIFEST"),
    "CanvasConnector": ("navox.connectors.builtin.canvas", "CanvasConnector"),
    "GOOGLE_MANIFEST": ("navox.connectors.builtin.google", "GOOGLE_MANIFEST"),
    "ensure_google_connector_connection": (
        "navox.connectors.builtin.google",
        "ensure_google_connector_connection",
    ),
    "google_canonical_resource": ("navox.connectors.builtin.google", "google_canonical_resource"),
    "mirror_google_document": ("navox.connectors.builtin.google", "mirror_google_document"),
}

__all__ = [
    "IMPORT_MANIFEST",
    "ImportConnector",
    "CANVAS_MANIFEST",
    "CanvasConnector",
    "GOOGLE_MANIFEST",
    "ensure_google_connector_connection",
    "google_canonical_resource",
    "mirror_google_document",
]

# Reload refreshes canonical exports rather than retaining cached old objects.
for _export in __all__:
    globals().pop(_export, None)

if _TYPE_CHECKING:
    from navox.connectors.builtin.canvas import CANVAS_MANIFEST, CanvasConnector
    from navox.connectors.builtin.google import (
        GOOGLE_MANIFEST,
        ensure_google_connector_connection,
        google_canonical_resource,
        mirror_google_document,
    )
    from navox.connectors.builtin.imports import IMPORT_MANIFEST, ImportConnector


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
