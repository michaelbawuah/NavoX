"""CI must fail closed on source checks, missing shards and invalid evidence."""

import importlib.util
import itertools
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def workflow():
    return yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)


def test_static_quality_starts_independently_and_remains_required_by_api_quality():
    jobs = workflow()["jobs"]
    static, aggregate = jobs["api-static"], jobs["api"]
    assert not static.get("needs") and not static.get("services")
    assert not static.get("if") and static.get("continue-on-error", "false") == "false"
    assert all(
        not step.get("if") and step.get("continue-on-error", "false") == "false"
        for step in static["steps"]
    )
    assert aggregate["name"] == "API quality"
    assert set(aggregate["needs"]) == {"api-static", "api-tests"}
    assert aggregate["if"] == "${{ always() }}"
    assert aggregate.get("continue-on-error", "false") == "false"
    commands = [step.get("run", "") for step in static["steps"]]
    for command in (
        "uv sync --locked --all-groups",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run mypy navox",
    ):
        assert command in commands
        step = next(step for step in static["steps"] if step.get("run") == command)
        assert step["working-directory"] == "services/api"
        assert not step.get("if") and step.get("continue-on-error", "false") == "false"
    assert any("ruff check ../../.github/scripts/api_test_shards.py" in run for run in commands)
    assert any(
        "ruff format --check ../../.github/scripts/api_test_shards.py" in run for run in commands
    )
    assert any("pytest" in run and "test_api_ci_contract.py" in run for run in commands)
    assert not any(
        "ruff " in step.get("run", "") or "mypy " in step.get("run", "")
        for step in aggregate["steps"]
    )


@pytest.mark.parametrize(
    "static_result,shard_result",
    tuple(itertools.product(("success", "failure", "cancelled", "skipped"), repeat=2)),
)
def test_required_aggregate_gate_rejects_every_non_success_result(static_result, shard_result):
    guard = workflow()["jobs"]["api"]["steps"][0]
    assert guard["env"] == {
        "STATIC_QUALITY_RESULT": "${{ needs.api-static.result }}",
        "TEST_SHARDS_RESULT": "${{ needs.api-tests.result }}",
    }
    assert not guard.get("if") and guard.get("continue-on-error", "false") == "false"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", guard["run"]],
        env={"STATIC_QUALITY_RESULT": static_result, "TEST_SHARDS_RESULT": shard_result},
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert (result.returncode == 0) is (static_result == shard_result == "success")


def test_six_shards_evidence_thresholds_and_migrations_are_still_enforced():
    jobs = workflow()["jobs"]
    assert jobs["api-tests"]["strategy"] == {
        "fail-fast": "false",
        "matrix": {"shard": [str(index) for index in range(6)]},
    }
    steps = jobs["api"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    for required in (
        "api_test_shards.py merge --count 6",
        "navox.evaluation.connector_metrics",
        "navox.evaluation.subscription_metrics",
        "uv run alembic upgrade head --sql",
        "uv run alembic downgrade 0015_spec_003_connectors",
    ):
        assert required in " ".join(commands.split())
    download = next(step for step in steps if step.get("uses") == "actions/download-artifact@v4")
    assert download["with"]["pattern"] == "navox-api-tests-shard-*"
    assert download["with"]["merge-multiple"] == "true"
    uploads = [step for step in steps if step.get("uses") == "actions/upload-artifact@v4"]
    assert {step["with"]["name"] for step in uploads} == {
        "navox-connector-measurements",
        "navox-subscription-measurements",
    }
    assert all(step["with"]["if-no-files-found"] == "error" for step in uploads)
    for step in steps:
        command = step.get("run", "")
        if any(
            gate in command
            for gate in ("api_test_shards.py merge", "navox.evaluation.", "alembic ")
        ):
            assert step.get("if") in {None, "always()"}
            assert step.get("continue-on-error", "false") == "false"


@pytest.fixture
def shard_evidence(tmp_path, monkeypatch):
    specification = importlib.util.spec_from_file_location(
        "api_ci_shard_helper", ROOT / ".github/scripts/api_test_shards.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    head = "a" * 40
    monkeypatch.setattr(module, "source_head", lambda: head)
    selected = {}
    candidate = 0
    while len(selected) < 6:
        node = f"tests/test_example_{candidate}.py::test_case"
        selected.setdefault(module.shard_for(node, 6), node)
        candidate += 1
    collected = sorted(selected.values())
    for index in range(6):
        (tmp_path / f"navox-api-shard-{index}.json").write_text(
            json.dumps(
                {
                    "version": module.VERSION,
                    "head": head,
                    "index": index,
                    "count": 6,
                    "exit_code": 0,
                    "collected": collected,
                    "selected": [selected[index]],
                }
            )
        )
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite", tests="1")
        case = ET.SubElement(
            suite, "testcase", classname=f"owned.shard{index}", name="test_case", time="0.1"
        )
        properties = ET.SubElement(case, "properties")
        ET.SubElement(properties, "property", name="measurement", value=f"shard-{index}")
        ET.ElementTree(root).write(tmp_path / f"navox-api-tests-{index}.xml")
    return module, tmp_path


def test_complete_shard_merge_retains_every_measurement_property(shard_evidence):
    module, directory = shard_evidence
    destination = directory / "merged.xml"
    result = module.merge_shards(directory, 6, destination)
    merged = ET.parse(destination).getroot()
    assert result["tests"] == 6
    assert merged.attrib["failures"] == merged.attrib["errors"] == merged.attrib["skipped"] == "0"
    assert {item.get("value") for item in merged.iter("property")} == {
        f"shard-{index}" for index in range(6)
    }


@pytest.mark.parametrize(
    "mutation",
    (
        "missing",
        "failed",
        "skipped",
        "junit_failure",
        "junit_error",
        "stale_head",
        "incomplete",
        "duplicate_junit",
    ),
)
def test_invalid_shard_evidence_cannot_pass_aggregate(shard_evidence, mutation):
    module, directory = shard_evidence
    path = directory / "navox-api-shard-0.json"
    if mutation == "missing":
        path.unlink()
    elif mutation in {"failed", "stale_head", "incomplete"}:
        manifest = json.loads(path.read_text())
        manifest[
            {"failed": "exit_code", "stale_head": "head", "incomplete": "selected"}[mutation]
        ] = {
            "failed": 1,
            "stale_head": "b" * 40,
            "incomplete": [],
        }[mutation]
        path.write_text(json.dumps(manifest))
    else:
        path = directory / "navox-api-tests-0.xml"
        root = ET.parse(path)
        case = root.find(".//testcase")
        if mutation in {"skipped", "junit_failure", "junit_error"}:
            ET.SubElement(case, mutation.removeprefix("junit_"))
        else:
            case.set("classname", "owned.shard1")
        root.write(path)
    with pytest.raises((ValueError, FileNotFoundError)):
        module.merge_shards(directory, 6, directory / "rejected.xml")
    assert not (directory / "rejected.xml").exists()
