"""The public connector API stays compatible without loading it into parser children."""

import importlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

PUBLIC_EXPORTS = {
    "navox.connectors": {
        "NAVOX_CONNECTOR_API_VERSION": "contracts",
        "CanonicalResource": "contracts",
        "CapabilityGateway": "capabilities",
        "ConnectorCapabilities": "contracts",
        "ConnectorConnectionContext": "contracts",
        "ConnectorHealth": "contracts",
        "ConnectorManifest": "contracts",
        "ConnectorRegistry": "registry",
        "ConnectorRuntime": "runtime",
        "ConnectorRuntimeError": "contracts",
        "EffectiveCapabilities": "capabilities",
        "NavoXConnector": "contracts",
        "ScopedSecretLease": "secrets",
        "SecretBroker": "secrets",
        "SecretBrokerError": "secrets",
        "stable_resource_id": "contracts",
    },
    "navox.connectors.builtin": {
        "IMPORT_MANIFEST": "imports",
        "ImportConnector": "imports",
        "CANVAS_MANIFEST": "canvas",
        "CanvasConnector": "canvas",
        "GOOGLE_MANIFEST": "google",
        "ensure_google_connector_connection": "google",
        "google_canonical_resource": "google",
        "mirror_google_document": "google",
    },
}


@pytest.mark.parametrize(
    "target", ["navox.connectors", "navox.connectors.builtin", "navox.lms_feeds.parse_child"]
)
def test_cold_package_and_parser_imports_do_not_load_runtime_database_or_other_builtins(target):
    # A fresh interpreter is essential: regular pytest collection has already
    # imported these modules for other tests, which would hide eager imports.
    program = """
import importlib.abc,sys
blocked=('navox.db','navox.connectors.runtime','navox.connectors.secrets',
         'navox.connectors.builtin.canvas','navox.connectors.builtin.google',
         'navox.connectors.builtin.imports')
class RejectUnrelatedImports(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        for name in blocked:
            assert fullname!=name and not fullname.startswith(name+'.'), fullname
sys.meta_path.insert(0,RejectUnrelatedImports())
importlib.import_module(sys.argv[1])
for name in ('navox.connectors','navox.connectors.builtin'):
    package=importlib.import_module(name)
    assert set(package.__all__) <= set(dir(package))
    assert not hasattr(package,'not_a_public_connector_export')
assert not any(name==prefix or name.startswith(prefix+'.')
               for name in sys.modules for prefix in blocked)
"""
    api_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, target],
        cwd=api_root,
        env={"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONPATH": str(api_root)},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("package_name", PUBLIC_EXPORTS)
def test_public_exports_keep_all_identity_star_import_and_introspection(package_name):
    package = importlib.import_module(package_name)
    expected = PUBLIC_EXPORTS[package_name]
    assert package.__all__ == list(expected)
    assert set(expected) <= set(dir(package))
    members = dict(inspect.getmembers(package))
    namespace = {}
    exec(f"from {package_name} import *", namespace)
    for name, module_name in expected.items():
        canonical = getattr(importlib.import_module(f"{package_name}.{module_name}"), name)
        assert getattr(package, name) is getattr(package, name) is canonical
        assert members[name] is namespace[name] is canonical
    with pytest.raises(AttributeError, match="has no attribute"):
        assert package.not_a_public_connector_export is None


@pytest.mark.parametrize(
    ("package_name", "name", "module_name"),
    [
        ("navox.connectors", "CapabilityGateway", "capabilities"),
        ("navox.connectors.builtin", "CanvasConnector", "canvas"),
    ],
)
def test_reload_refreshes_public_exports_to_the_canonical_objects(
    monkeypatch, package_name, name, module_name
):
    package = importlib.import_module(package_name)
    canonical = getattr(importlib.import_module(f"{package_name}.{module_name}"), name)
    monkeypatch.setattr(package, name, object())
    importlib.reload(package)
    assert getattr(package, name) is canonical


def test_existing_submodule_from_imports_still_resolve_modules():
    from navox.connectors import runtime as runtime_module
    from navox.connectors.builtin import canvas as canvas_module

    assert runtime_module is importlib.import_module("navox.connectors.runtime")
    assert canvas_module is importlib.import_module("navox.connectors.builtin.canvas")
