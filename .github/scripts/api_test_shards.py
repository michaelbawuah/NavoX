"""Run isolated API test shards and verify complete JUnit evidence before merging."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

API_ROOT = Path(__file__).resolve().parents[2] / "services" / "api"
TIMING_WEIGHTS = Path(__file__).with_name("api_test_file_weights.json")
VERSION = 2


def is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"[a-f0-9]{{{length}}}", value) is not None


def load_timing_weights(
    path: Path | None = None,
) -> tuple[dict[str, tuple[int, int]], str]:
    """Load reviewed, frozen scheduling hints; never fetch or change them in CI."""
    raw = (path or TIMING_WEIGHTS).read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError("API timing weights exceed their size bound")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate API timing weight key")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "source", "artifacts", "files"}
        or value["schema"] != "navox-api-file-timings.v1"
    ):
        raise ValueError("Invalid API timing weight schema")
    source, artifacts, files = value["source"], value["artifacts"], value["files"]
    if (
        not isinstance(source, dict)
        or source.get("repository") != "michaelbawuah/NavoX"
        or type(source.get("workflow_run_id")) is not int
        or source["workflow_run_id"] <= 0
        or not is_hex(source.get("checkout_sha"), 40)
        or not is_hex(source.get("collection_sha256"), 64)
        or type(source.get("collected_tests")) is not int
        or source["collected_tests"] <= 0
        or not isinstance(source.get("measurement"), str)
        or not source["measurement"]
        or not isinstance(source.get("purpose"), str)
        or not source["purpose"]
        or not isinstance(artifacts, list)
        or len(artifacts) != 6
        or not isinstance(files, dict)
        or not files
    ):
        raise ValueError("Invalid API timing provenance")
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or type(item.get("shard")) is not int
            or item["shard"] not in range(6)
            or type(item.get("artifact_id")) is not int
            or item["artifact_id"] <= 0
            or type(item.get("cases")) is not int
            or item["cases"] <= 0
            or any(
                not is_hex(item.get(key), 64)
                for key in ("archive_sha256", "junit_sha256", "manifest_sha256")
            )
        ):
            raise ValueError("Invalid API timing artifact provenance")
    if (
        sorted(item["shard"] for item in artifacts) != list(range(6))
        or len({item["artifact_id"] for item in artifacts}) != 6
    ):
        raise ValueError("API timings require six distinct source artifacts")
    timings = {}
    for filename, item in files.items():
        if (
            not re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py", filename)
            or not isinstance(item, dict)
            or set(item) != {"milliseconds", "cases"}
            or type(item["milliseconds"]) is not int
            or item["milliseconds"] < 0
            or type(item["cases"]) is not int
            or item["cases"] <= 0
        ):
            raise ValueError("Invalid API file timing")
        timings[filename] = (item["milliseconds"], item["cases"])
    if (
        sum(cases for _, cases in timings.values()) != source["collected_tests"]
        or sum(item["cases"] for item in artifacts) != source["collected_tests"]
    ):
        raise ValueError("API timing provenance has inconsistent case counts")
    return timings, hashlib.sha256(raw).hexdigest()


def plan_shards(
    collected: list[str], count: int, timings: dict[str, tuple[int, int]]
) -> tuple[dict[str, int], list[int]]:
    """Balance whole files using integer estimates, with stable filename/index ties.

    Scale known files by their collected case count. New files use the frozen
    baseline's mean time per case. These estimates only choose a serial process;
    they never cap execution, skip tests or determine whether a test passes.
    """
    if type(count) is not int or not 1 <= count <= 16 or not collected:
        raise ValueError("Invalid API shard planning input")
    if len(collected) != len(set(collected)):
        raise ValueError("Duplicate API test node IDs")
    if not timings or any(
        type(milliseconds) is not int or milliseconds < 0 or type(cases) is not int or cases <= 0
        for milliseconds, cases in timings.values()
    ):
        raise ValueError("Invalid API shard timing estimates")
    files = Counter(node.split("::", 1)[0] for node in collected)
    baseline_ms = sum(milliseconds for milliseconds, _ in timings.values())
    baseline_cases = sum(cases for _, cases in timings.values())
    estimates = {}
    for filename, cases in files.items():
        milliseconds, measured_cases = timings.get(filename, (baseline_ms, baseline_cases))
        estimates[filename] = max(1, (milliseconds * cases + measured_cases - 1) // measured_cases)
    loads = [0] * count
    assignments = {}
    for filename in sorted(files, key=lambda name: (-estimates[name], name)):
        index = min(range(count), key=lambda index: (loads[index], index))
        assignments[filename] = index
        loads[index] += estimates[filename]
    return assignments, loads


def plan_digest(assignments: dict[str, int], estimates: list[int]) -> str:
    value = {"files": assignments, "estimated_shard_milliseconds": estimates}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=API_ROOT, text=True).strip()


def run_shard(index: int, count: int, output_dir: Path) -> int:
    import pytest

    timings, timing_digest = load_timing_weights()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "version": VERSION,
        "head": source_head(),
        "index": index,
        "count": count,
        "collected": [],
        "selected": [],
        "timing_weights_sha256": timing_digest,
        "assignment_sha256": None,
        "estimated_shard_milliseconds": [],
    }

    class FileShard:
        def pytest_collection_modifyitems(self, config, items):
            collected = [item.nodeid for item in items]
            if len(collected) != len(set(collected)):
                raise pytest.UsageError("Duplicate API test node IDs")
            assignments, estimates = plan_shards(collected, count, timings)
            selected = [
                item for item in items if assignments[item.nodeid.split("::", 1)[0]] == index
            ]
            deselected = [
                item for item in items if assignments[item.nodeid.split("::", 1)[0]] != index
            ]
            manifest["collected"] = sorted(collected)
            manifest["selected"] = sorted(item.nodeid for item in selected)
            manifest["assignment_sha256"] = plan_digest(assignments, estimates)
            manifest["estimated_shard_milliseconds"] = estimates
            config.hook.pytest_deselected(items=deselected)
            items[:] = selected

    result = int(
        pytest.main(
            [
                str(API_ROOT / "tests"),
                f"--junitxml={output_dir / f'navox-api-tests-{index}.xml'}",
                "--durations=30",
            ],
            plugins=[FileShard()],
        )
    )
    manifest["exit_code"] = result
    (output_dir / f"navox-api-shard-{index}.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return result


def merge_shards(input_dir: Path, count: int, output: Path) -> dict[str, object]:
    expected_head = source_head()
    timings, timing_digest = load_timing_weights()
    collected: list[str] | None = None
    seen_nodes: set[str] = set()
    seen_cases: set[tuple[str, str]] = set()
    combined = ET.Element("testsuites")
    seconds = 0.0
    for index in range(count):
        manifest = json.loads(
            (input_dir / f"navox-api-shard-{index}.json").read_text(encoding="utf-8")
        )
        if (
            manifest.get("version") != VERSION
            or manifest.get("head") != expected_head
            or manifest.get("index") != index
            or manifest.get("count") != count
            or manifest.get("exit_code") != 0
            or manifest.get("timing_weights_sha256") != timing_digest
        ):
            raise ValueError(f"API shard {index} has invalid or failed execution evidence")
        current = manifest.get("collected")
        if (
            not isinstance(current, list)
            or not current
            or not all(isinstance(node, str) for node in current)
            or current != sorted(set(current))
        ):
            raise ValueError(f"API shard {index} has invalid full-suite collection")
        if collected is None:
            collected = current
        elif current != collected:
            raise ValueError(f"API shard {index} collected a different test suite")
        assignments, estimates = plan_shards(current, count, timings)
        if (
            manifest.get("assignment_sha256") != plan_digest(assignments, estimates)
            or manifest.get("estimated_shard_milliseconds") != estimates
            or any(type(value) is not int for value in manifest["estimated_shard_milliseconds"])
        ):
            raise ValueError(f"API shard {index} has inconsistent assignment evidence")
        expected = [node for node in current if assignments[node.split("::", 1)[0]] == index]
        if not expected or manifest.get("selected") != expected:
            raise ValueError(f"API shard {index} has incomplete test selection")
        if seen_nodes.intersection(expected):
            raise ValueError("API shards contain overlapping selections")
        seen_nodes.update(expected)
        root = ET.parse(input_dir / f"navox-api-tests-{index}.xml").getroot()
        cases = list(root.iter("testcase"))
        if len(cases) != len(expected):
            raise ValueError(f"API shard {index} has incomplete JUnit results")
        for case in cases:
            if any(case.find(tag) is not None for tag in ("error", "failure", "skipped")):
                raise ValueError(f"API shard {index} contains failed or skipped tests")
            key = (case.get("classname", ""), case.get("name", ""))
            if not all(key) or key in seen_cases:
                raise ValueError("API shards contain invalid or duplicate JUnit cases")
            seen_cases.add(key)
            seconds += float(case.get("time", "0"))
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        if not suites:
            raise ValueError(f"API shard {index} is missing a JUnit test suite")
        combined.extend(suites)
    if collected is None or seen_nodes != set(collected):
        raise ValueError("API shards do not cover the complete collected suite")
    combined.attrib.update(
        tests=str(len(seen_cases)),
        failures="0",
        errors="0",
        skipped="0",
        time=str(seconds),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(combined).write(output, encoding="utf-8", xml_declaration=True)
    return {
        "head": expected_head,
        "shards": count,
        "tests": len(seen_cases),
        "junit": str(output),
        "timing_weights_sha256": timing_digest,
        "estimated_shard_milliseconds": estimates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--index", type=int, required=True)
    run.add_argument("--count", type=int, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    merge = commands.add_parser("merge")
    merge.add_argument("--count", type=int, required=True)
    merge.add_argument("--input-dir", type=Path, required=True)
    merge.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.count <= 16:
        parser.error("Shard count must be between 1 and 16")
    if args.command == "run":
        if not 0 <= args.index < args.count:
            parser.error("Shard index is outside the configured count")
        return run_shard(args.index, args.count, args.output_dir)
    print(json.dumps(merge_shards(args.input_dir, args.count, args.output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
