"""Run isolated API test shards and verify complete JUnit evidence before merging."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

API_ROOT = Path(__file__).resolve().parents[2] / "services" / "api"
VERSION = 1


def shard_for(node_id: str, count: int) -> int:
    # Keep each file's fixtures and temporal scenarios in one serial process.
    filename = node_id.split("::", 1)[0]
    return int(hashlib.sha256(filename.encode()).hexdigest(), 16) % count


def source_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=API_ROOT, text=True
    ).strip()


def run_shard(index: int, count: int, output_dir: Path) -> int:
    import pytest

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "version": VERSION,
        "head": source_head(),
        "index": index,
        "count": count,
        "collected": [],
        "selected": [],
    }

    class FileShard:
        def pytest_collection_modifyitems(self, config, items):
            collected = [item.nodeid for item in items]
            if len(collected) != len(set(collected)):
                raise pytest.UsageError("Duplicate API test node IDs")
            selected = [
                item for item in items if shard_for(item.nodeid, count) == index
            ]
            deselected = [
                item for item in items if shard_for(item.nodeid, count) != index
            ]
            manifest["collected"] = sorted(collected)
            manifest["selected"] = sorted(item.nodeid for item in selected)
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
        ):
            raise ValueError(
                f"API shard {index} has invalid or failed execution evidence"
            )
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
        expected = [node for node in current if shard_for(node, count) == index]
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
            if any(
                case.find(tag) is not None for tag in ("error", "failure", "skipped")
            ):
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
