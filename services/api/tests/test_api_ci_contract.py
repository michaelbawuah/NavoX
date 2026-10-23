"""CI must fail closed on source checks, missing shards and invalid evidence."""

import hashlib
import importlib.util
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
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
def shard_module():
    specification = importlib.util.spec_from_file_location(
        "api_ci_shard_helper", ROOT / ".github/scripts/api_test_shards.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def shard_evidence(tmp_path, monkeypatch, shard_module):
    module = shard_module
    head = "a" * 40
    monkeypatch.setattr(module, "source_head", lambda: head)
    collected = [f"tests/test_example_{index}.py::test_case" for index in range(6)]
    timings, timing_digest = module.load_timing_weights()
    assignments, estimates = module.plan_shards(collected, 6, timings)
    selected = {assignments[node.split("::", 1)[0]]: node for node in collected}
    assert len(selected) == 6
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
                    "timing_weights_sha256": timing_digest,
                    "assignment_sha256": module.plan_digest(assignments, estimates),
                    "estimated_shard_milliseconds": estimates,
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
        "stale_version",
        "changed_timing_weights",
        "changed_assignment",
        "changed_estimates",
    ),
)
def test_invalid_shard_evidence_cannot_pass_aggregate(shard_evidence, mutation):
    module, directory = shard_evidence
    path = directory / "navox-api-shard-0.json"
    if mutation == "missing":
        path.unlink()
    elif mutation in {
        "failed",
        "stale_head",
        "incomplete",
        "stale_version",
        "changed_timing_weights",
        "changed_assignment",
        "changed_estimates",
    }:
        manifest = json.loads(path.read_text())
        manifest[
            {
                "failed": "exit_code",
                "stale_head": "head",
                "incomplete": "selected",
                "stale_version": "version",
                "changed_timing_weights": "timing_weights_sha256",
                "changed_assignment": "assignment_sha256",
                "changed_estimates": "estimated_shard_milliseconds",
            }[mutation]
        ] = {
            "failed": 1,
            "stale_head": "b" * 40,
            "incomplete": [],
            "stale_version": module.VERSION - 1,
            "changed_timing_weights": "b" * 64,
            "changed_assignment": "b" * 64,
            "changed_estimates": [0] * 6,
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


def test_frozen_timings_are_traceable_and_estimates_balance_the_completed_run(shard_module):
    timings, checksum = shard_module.load_timing_weights()
    raw = shard_module.TIMING_WEIGHTS.read_bytes()
    source = json.loads(raw)
    assert checksum == hashlib.sha256(raw).hexdigest()
    assert source["source"]["workflow_run_id"] == 39867875715
    assert source["source"]["checkout_sha"] == "b9bbb4b5bfe4bbbd3cd98bc04db54f2d51185521"
    assert {item["shard"] for item in source["artifacts"]} == set(range(6))
    assert sum(cases for _, cases in timings.values()) == 4503
    collected = [
        f"{filename}::test_frozen_case_{index}"
        for filename, (_, cases) in timings.items()
        for index in range(cases)
    ]
    assignments, estimates = shard_module.plan_shards(collected, 6, timings)
    old_loads = [0] * 6
    for filename, (milliseconds, _) in timings.items():
        old_loads[int(hashlib.sha256(filename.encode()).hexdigest(), 16) % 6] += milliseconds
    assert set(assignments) == set(timings)
    assert sum(estimates) == sum(max(1, milliseconds) for milliseconds, _ in timings.values())
    assert max(estimates) < max(old_loads) * 0.6
    # This is a scheduling estimate on frozen work, not a promised runtime threshold.
    assert "estimates" in source["source"]["purpose"]


def test_balancing_keeps_each_file_serial_and_covers_new_files_exactly_once(shard_module):
    timings = {
        "tests/test_heavy.py": (6000, 6),
        "tests/test_small.py": (100, 1),
        "tests/test_deleted.py": (12000, 3),
    }
    collected = [f"tests/test_heavy.py::test_case[{index}]" for index in range(6)]
    collected += ["tests/test_small.py::test_case"]
    collected += [f"tests/test_new.py::test_case[{index}]" for index in range(20)]
    assignments, loads = shard_module.plan_shards(collected, 6, timings)
    assert set(assignments) == {node.split("::", 1)[0] for node in collected}
    assert "tests/test_deleted.py" not in assignments
    selected = [
        [node for node in collected if assignments[node.split("::", 1)[0]] == index]
        for index in range(6)
    ]
    assert sorted(node for selection in selected for node in selection) == sorted(collected)
    assert sum(len(selection) for selection in selected) == len(set(collected))
    assert (
        len({assignments[node.split("::", 1)[0]] for node in collected if "test_new.py" in node})
        == 1
    )
    assert sum(loads) == 6000 + 100 + 36200


def test_known_weights_scale_and_unknown_mean_uses_entire_frozen_baseline(shard_module):
    timings = {"tests/test_known.py": (100, 2), "tests/test_deleted.py": (1000, 2)}
    assignments, estimates = shard_module.plan_shards(
        [f"tests/test_known.py::test_case[{index}]" for index in range(6)], 1, timings
    )
    assert assignments == {"tests/test_known.py": 0}
    assert estimates == [300]
    _, estimates = shard_module.plan_shards(["tests/test_new.py::test_case"], 1, timings)
    assert estimates == [275]
    _, estimates = shard_module.plan_shards(
        ["tests/test_zero.py::test_case"], 1, {"tests/test_zero.py": (0, 1)}
    )
    assert estimates == [1]


def test_equal_weight_ties_use_filename_then_shard_index(shard_module):
    timings = {f"tests/test_{name}.py": (100, 1) for name in ("gamma", "beta", "alpha")}
    collected = [f"{filename}::test_case" for filename in timings]
    assignments, loads = shard_module.plan_shards(collected, 2, timings)
    assert assignments == {
        "tests/test_alpha.py": 0,
        "tests/test_beta.py": 1,
        "tests/test_gamma.py": 0,
    }
    assert loads == [200, 100]
    assert shard_module.plan_shards(
        list(reversed(collected)), 2, dict(reversed(timings.items()))
    ) == (
        assignments,
        loads,
    )


def test_skewed_legacy_file_hash_does_not_concentrate_expensive_files(shard_module):
    filenames = []
    index = 0
    while len(filenames) < 18:
        filename = f"tests/test_skew_{index}.py"
        if int(hashlib.sha256(filename.encode()).hexdigest(), 16) % 6 == 5:
            filenames.append(filename)
        index += 1
    timings = {filename: (6000 if index < 6 else 10, 1) for index, filename in enumerate(filenames)}
    assignments, loads = shard_module.plan_shards(
        [f"{filename}::test_case" for filename in filenames], 6, timings
    )
    assert len(assignments) == len(filenames)
    assert max(loads) <= 6020
    assert max(loads) < sum(weight for weight, _ in timings.values()) / 5


def test_schedule_and_digest_are_stable_across_hash_seeds(shard_module):
    script = """
import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('ci_helper',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
timings,_=module.load_timing_weights()
nodes=list({f'{filename}::test_seed_{i}' for filename in timings for i in range(2)})
nodes+=['tests/test_new_seed.py::test_case']
assignments,loads=module.plan_shards(nodes,6,timings)
print(json.dumps({'files':assignments,'loads':loads,'digest':module.plan_digest(assignments,loads)},sort_keys=True))
"""
    outputs = []
    for seed in ("101", "202"):
        outputs.append(
            subprocess.run(
                [sys.executable, "-c", script, str(ROOT / ".github/scripts/api_test_shards.py")],
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout
        )
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_run_collection_uses_same_complete_plan_as_merge(
    shard_module, monkeypatch, tmp_path, exit_code
):
    module = shard_module
    timings = {"tests/test_known.py": (900, 3)}
    collected = [f"tests/test_known.py::test_case[{index}]" for index in range(3)]
    collected += [f"tests/test_new_{index}.py::test_case" for index in range(8)]
    assignments, estimates = module.plan_shards(collected, 6, timings)
    chosen = 2
    expected = sorted(node for node in collected if assignments[node.split("::", 1)[0]] == chosen)
    assert expected
    monkeypatch.setattr(module, "source_head", lambda: "a" * 40)
    monkeypatch.setattr(module, "load_timing_weights", lambda: (timings, "b" * 64))

    def fake_pytest_main(arguments, *, plugins):
        assert "--durations=30" in arguments
        assert any(argument.startswith("--junitxml=") for argument in arguments)
        items = [SimpleNamespace(nodeid=node) for node in reversed(collected)]
        deselected = []
        config = SimpleNamespace(
            hook=SimpleNamespace(pytest_deselected=lambda *, items: deselected.extend(items))
        )
        plugins[0].pytest_collection_modifyitems(config, items)
        assert sorted(item.nodeid for item in items) == expected
        assert sorted(item.nodeid for item in [*items, *deselected]) == sorted(collected)
        return exit_code

    monkeypatch.setattr(pytest, "main", fake_pytest_main)
    assert module.run_shard(chosen, 6, tmp_path) == exit_code
    manifest = json.loads((tmp_path / f"navox-api-shard-{chosen}.json").read_text())
    assert manifest["collected"] == sorted(collected)
    assert manifest["selected"] == expected
    assert manifest["assignment_sha256"] == module.plan_digest(assignments, estimates)
    assert manifest["estimated_shard_milliseconds"] == estimates
    assert manifest["timing_weights_sha256"] == "b" * 64
    assert manifest["exit_code"] == exit_code


@pytest.mark.parametrize(
    "change",
    [
        "schema",
        "run",
        "source",
        "source_type",
        "artifact_hash",
        "artifact_repeat",
        "count",
        "weight",
        "bool_weight",
        "bool_count",
        "count_total",
    ],
)
def test_malformed_weight_provenance_cannot_silently_change_selection(
    shard_module, tmp_path, change
):
    value = json.loads(shard_module.TIMING_WEIGHTS.read_text())
    filename = next(iter(value["files"]))
    if change == "schema":
        value["schema"] = "unreviewed"
    elif change == "run":
        value["source"]["workflow_run_id"] = True
    elif change == "source":
        value["source"]["checkout_sha"] = "missing"
    elif change == "source_type":
        value["source"]["checkout_sha"] = int("1" * 40)
    elif change == "artifact_hash":
        value["artifacts"][0]["junit_sha256"] = "missing"
    elif change == "artifact_repeat":
        value["artifacts"][0] = value["artifacts"][1]
    elif change == "count":
        value["files"][filename]["cases"] = 0
    elif change == "weight":
        value["files"][filename]["milliseconds"] = -1
    elif change == "bool_weight":
        value["files"][filename]["milliseconds"] = True
    elif change == "bool_count":
        value["files"][filename]["cases"] = True
    else:
        value["source"]["collected_tests"] += 1
    path = tmp_path / "invalid-weights.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        shard_module.load_timing_weights(path)


def test_duplicate_weight_keys_cannot_replace_reviewed_values(shard_module, tmp_path):
    path = tmp_path / "duplicate-weights.json"
    path.write_text(
        shard_module.TIMING_WEIGHTS.read_text().replace('"files": {', '"files": {}, "files": {', 1)
    )
    with pytest.raises(ValueError, match="Duplicate"):
        shard_module.load_timing_weights(path)


@pytest.mark.parametrize("count", [0, 17, True])
def test_invalid_plans_fail_before_selection(shard_module, count):
    with pytest.raises(ValueError):
        shard_module.plan_shards(["tests/test_new.py::test_case"], count, {"fixture": (1, 1)})
    with pytest.raises(ValueError):
        shard_module.plan_shards(["tests/test_new.py::test_case"] * 2, 6, {"fixture": (1, 1)})
