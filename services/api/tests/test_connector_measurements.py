"""Real refresh decoders measured with synthetic HTTP responses."""

import json
from urllib.parse import parse_qs
from xml.etree import ElementTree as ET

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.connectors import canvas_oauth
from navox.core.credential_vault import CredentialVault
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import Connection, ConnectionCredential, User, Workspace
from navox.evaluation.connector_metrics import PROPERTY, TARGETS, build_report, meets_target
from navox.evaluation.runner import project_root
from navox.providers import google_oauth


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "canvas"])
@pytest.mark.parametrize(
    "variant", ["standard", "string_expiry", "narrow_scope", "rotated_refresh"]
)
async def test_measured_valid_oauth_refresh(provider, variant, monkeypatch, measure):
    access, refresh = "synthetic-access-for-measurement", "synthetic-refresh-for-measurement"
    body = {"access_token": access, "token_type": "Bearer", "expires_in": 3600, "user": {"id": 7}}
    if variant == "string_expiry":
        body["expires_in"] = "1800"
    if variant == "narrow_scope":
        body["scope"] = "read"
    if variant == "rotated_refresh":
        body["refresh_token"] = "synthetic-new-refresh"
    requests = []

    async def http(request):
        data = parse_qs(request.content.decode())
        assert data["grant_type"] == ["refresh_token"] and data["refresh_token"] == [refresh]
        requests.append(request.url.path)
        return httpx.Response(200, json=body)

    client_class = httpx.AsyncClient
    if provider == "canvas":
        monkeypatch.setattr(
            canvas_oauth, "safe_client", lambda _: client_class(transport=httpx.MockTransport(http))
        )
        deployment = canvas_oauth.CanvasDeployment(
            "https://canvas.example.invalid",
            "123",
            SecretStr("synthetic-client"),
            "https://navox.example.invalid/callback",
        )
        result = await canvas_oauth.exchange(deployment, refresh=refresh)
        success = result.access.get_secret_value() == access and result.user_id == "7"
    else:
        monkeypatch.setattr(
            google_oauth.httpx,
            "AsyncClient",
            lambda **kwargs: client_class(transport=httpx.MockTransport(http), **kwargs),
        )
        settings = Settings(
            _env_file=None,
            google_oauth_client_id="synthetic-client",
            google_oauth_client_secret="synthetic-secret",
            google_token_encryption_key=Fernet.generate_key().decode(),
        )
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with async_sessionmaker(engine)() as db:
                user, workspace = User(email="refresh@example.invalid"), Workspace(name="Refresh")
                credential = ConnectionCredential(
                    encrypted_refresh_token=CredentialVault(settings).seal_refresh_token(refresh)
                )
                db.add_all([user, workspace, credential])
                await db.flush()
                connection = Connection(
                    user_id=user.id,
                    workspace_id=workspace.id,
                    provider="google",
                    external_account_id="fixture",
                    status="active",
                    credential_reference=credential.id,
                    granted_scopes=["read", "write"],
                )
                db.add(connection)
                await db.flush()
                result = await google_oauth.access_token_for_connection(
                    db, connection=connection, settings=settings
                )
                success = result == access and connection.access_token_expires_at is not None
                if variant == "narrow_scope":
                    assert connection.granted_scopes == ["read"]
        finally:
            await engine.dispose()
    assert requests == (["/token"] if provider == "google" else ["/login/oauth2/token"])
    measure(
        "oauth_refresh",
        int(success),
        len(requests),
        f"{provider}: valid {variant} refresh response",
        database="sqlite" if provider == "google" else False,
    )
    assert success


def evidence_file(tmp_path):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    case = ET.SubElement(suite, "testcase", name="measurements", classname="tests.measurements")
    properties = ET.SubElement(case, "properties")
    for name, (operator, _, _) in TARGETS.items():
        ET.SubElement(
            properties,
            "property",
            name=PROPERTY,
            value=json.dumps(
                {
                    "metric": name,
                    "numerator": 1 if operator == ">=" else 0,
                    "denominator": 1,
                    "scenario": "reporter fixture",
                    "environment": "in_memory",
                }
            ),
        )
    gates = json.loads((project_root() / "evals/intelligence/gates.json").read_text())
    for name in gates["required_tests"]:
        ET.SubElement(suite, "testcase", name=name, classname="tests.test_intelligence_acceptance")
    path = tmp_path / "evidence.xml"
    return root, suite, case, path


@pytest.mark.parametrize(
    "fault",
    [None, "missing", "zero", "inflated", "duplicate", "skipped", "failed", "missing_regression"],
)
def test_measurement_report_fails_closed_for_incomplete_or_invalid_evidence(tmp_path, fault):
    root, suite, case, path = evidence_file(tmp_path)
    props = case.find("properties")
    first = props[0]
    if fault == "missing":
        props.remove(first)
    elif fault in {"zero", "inflated"}:
        sample = json.loads(first.get("value"))
        sample["denominator" if fault == "zero" else "numerator"] = 0 if fault == "zero" else 100
        first.set("value", json.dumps(sample))
    elif fault == "duplicate":
        ET.SubElement(props, "property", **first.attrib)
    elif fault in {"skipped", "failed"}:
        ET.SubElement(case, "skipped" if fault == "skipped" else "failure")
    elif fault == "missing_regression":
        suite.remove(suite[-1])
    ET.ElementTree(root).write(path)
    report = build_report(path)
    assert report["passed"] is (fault is None)
    assert report["production_metrics"] is None
    if fault is None:
        assert report["metrics"]["spec_002_regressions"]["denominator"] == 4


def test_metric_thresholds_use_exact_fractions_and_strict_replay_bound():
    assert not meets_target(1, 100, "<", "0.01")
    assert meets_target(1, 101, "<", "0.01")
    assert meets_target(999, 1000, ">=", "0.999")
    assert not meets_target(998, 999, ">=", "0.999")
    assert not meets_target(0, 0, "==", "0")


def test_prepared_live_github_configuration_is_operator_approved_read_only():
    from navox.connectors.generic_registration import ApprovedGenericConfig

    path = project_root() / "examples/connectors/navox-github-issues.json"
    approved = ApprovedGenericConfig.model_validate_json(path.read_text())
    assert approved.config.base_url == "https://api.github.com"
    assert approved.config.auth == "bearer"
    assert approved.config.endpoints[0].path == "/repos/michaelbawuah/NavoX/issues"
    assert approved.manifest.capabilities.write == []
    assert len(approved.manifest.capabilities.read) == 1
    assert "API_TOKEN" not in path.read_text()
