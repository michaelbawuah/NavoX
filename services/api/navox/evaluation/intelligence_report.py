"""Report executed SPEC-002 acceptance tests without claiming live model accuracy."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from navox.evaluation.runner import load_json, project_root


def build_report(junit_path: Path, gates_path: Path) -> dict[str, Any]:
    gates = load_json(gates_path)
    root = ElementTree.parse(junit_path).getroot()
    results: dict[str, str] = {}
    passed = failed = skipped = 0
    for case in root.iter("testcase"):
        name = case.attrib.get("name", "")
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
            failed += 1
        elif case.find("skipped") is not None:
            outcome = "skipped"
            skipped += 1
        else:
            outcome = "passed"
            passed += 1
        # A duplicate name cannot hide a failed or skipped execution.
        if results.get(name) in {"failed", "skipped"}:
            continue
        results[name] = outcome
    required = {name: results.get(name, "missing") for name in gates["required_tests"]}
    executed = passed + failed
    gate_passed = (
        executed >= gates["minimum_executed_tests"]
        and failed <= gates["maximum_failed_tests"]
        and skipped <= gates["maximum_skipped_tests"]
        and all(outcome == "passed" for outcome in required.values())
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "evaluation_kind": gates["evaluation_kind"],
        "scope": gates["scope"],
        "test_counts": {
            "executed": executed,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "required_demos_and_security": required,
        "passed": gate_passed,
        "live_provider_quality_measured": False,
        "production_metrics": None,
        "limitations": [
            "The model and external sources are recorded synthetic fixtures.",
            "Counts measure application correctness; model precision and recall are unmeasured.",
            "Live Gmail/Calendar delivery and model quality require separately authorized runs.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--gates", type=Path, default=project_root() / "evals/intelligence/gates.json"
    )
    arguments = parser.parse_args()
    report = build_report(arguments.junit, arguments.gates)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "test_counts": report["test_counts"]}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
