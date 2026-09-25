"""Aggregate explicit SPEC-003 measurements, never infer rates from test totals."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from navox.evaluation.intelligence_report import build_report as intelligence_report
from navox.evaluation.runner import project_root

PROPERTY = "spec003.measurement"

# Numerators count either successful operations or observed violations, as named.
TARGETS: dict[str, tuple[str, str, str]] = {
    "contract_compliance": (">=", "1", "reference manifest round trips"),
    "canonical_validity": (">=", "1", "adapter resource schema validations"),
    "workspace_violations": ("==", "0", "wrong-owner/workspace broker operations"),
    "credential_leakage": ("==", "0", "credential-bearing output surfaces inspected"),
    "unauthorized_actions": ("==", "0", "ungranted adapter write attempts"),
    "replay_duplication": ("<", "0.01", "replayed canonical resource revisions"),
    "incremental_correctness": (">=", "0.99", "expected final resource states"),
    "cursor_recovery": (">=", "1", "injected interruption/recovery schedules"),
    "oauth_refresh": (">=", "0.99", "valid Google/Canvas refresh response scenarios"),
    "health_accuracy": (">=", "0.98", "provider health/error classifications"),
    "event_deduplication": (">=", "0.999", "authenticated duplicate deliveries"),
    "stable_identity": (">=", "0.999", "repeat resource mappings"),
    "secret_to_llm": ("==", "0", "model input documents inspected"),
    "cross_connector_credentials": ("==", "0", "borrowed credential operations"),
    "capability_bypass": ("==", "0", "missing-grant capability evaluations"),
}


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    numerator: StrictInt = Field(ge=0)
    denominator: StrictInt = Field(gt=0)
    scenario: str = Field(min_length=1, max_length=240)
    environment: Literal["sqlite", "postgresql", "in_memory"]

    @model_validator(mode="after")
    def valid_counts(self) -> Measurement:
        if self.metric not in TARGETS or self.numerator > self.denominator:
            raise ValueError("Invalid metric or counts")
        return self


def meets_target(numerator: int, denominator: int, operator: str, threshold: str) -> bool:
    if denominator <= 0:
        return False
    value, target = Fraction(numerator, denominator), Fraction(threshold)
    return {">=": value >= target, "<": value < target, "==": value == target}[operator]


def build_report(junit: Path) -> dict[str, object]:
    root = ElementTree.parse(junit).getroot()
    measurements: dict[str, list[dict[str, object]]] = {name: [] for name in TARGETS}
    seen: set[tuple[str, str]] = set()
    invalid = failed = skipped = executed = 0
    for case in root.iter("testcase"):
        failed += int(case.find("failure") is not None or case.find("error") is not None)
        skipped += int(case.find("skipped") is not None)
        executed += int(case.find("skipped") is None)
        identifier = f"{case.get('classname', '')}.{case.get('name', '')}"
        for prop in case.findall("properties/property"):
            if prop.get("name") != PROPERTY:
                continue
            try:
                sample = Measurement.model_validate_json(prop.get("value", ""))
            except ValueError:
                invalid += 1
                continue
            key = identifier, sample.metric
            if key in seen:
                invalid += 1
                continue
            seen.add(key)
            measurements[sample.metric].append({"test": identifier, **sample.model_dump()})
    metrics: dict[str, object] = {}
    outcomes = []
    for name, (operator, threshold, unit) in TARGETS.items():
        samples = measurements[name]
        numerator = sum(int(str(sample["numerator"])) for sample in samples)
        denominator = sum(int(str(sample["denominator"])) for sample in samples)
        passed = meets_target(numerator, denominator, operator, threshold)
        outcomes.append(passed)
        metrics[name] = {
            "numerator": numerator,
            "denominator": denominator,
            "rate": numerator / denominator if denominator else None,
            "target": f"{operator} {threshold}",
            "unit": unit,
            "passed": passed,
            "samples": samples,
        }
    regression = intelligence_report(
        junit,
        project_root() / "evals/intelligence/gates.json",
        classname_prefix="tests.test_intelligence_",
    )
    metrics["spec_002_regressions"] = {
        "passed": regression["passed"],
        "numerator": regression["test_counts"]["failed"],
        "denominator": regression["test_counts"]["executed"],
        "target": "== 0",
        "required_checks": regression["required_demos_and_security"],
        "unit": "executed SPEC-002 regression checks; not provider operations",
    }
    return {
        "schema_version": "spec-003-measurements.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "bounded synthetic fixtures; no live provider reliability measured",
        "production_metrics": None,
        "python": platform.python_version(),
        "junit_sha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
        "tests": {"executed": executed, "failed": failed, "skipped": skipped},
        "invalid_measurements": invalid,
        "metrics": metrics,
        "passed": bool(
            all(outcomes) and regression["passed"] and not (failed or skipped or invalid)
        ),
        "limitations": [
            "Sample rates describe the listed scenarios, not statistical production guarantees.",
            "Providers and model responses are fixtures; database engines are recorded per sample.",
            "Mandatory live demonstration D and historical-data review remain separate gates.",
        ],
    }


def source_revision() -> dict[str, object]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(project_root()), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        return {
            "commit": git("rev-parse", "HEAD"),
            "commit_tree": git("rev-parse", "HEAD^{tree}"),
            "dirty": bool(git("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "commit_tree": None, "dirty": None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.junit)
    report["source"] = source_revision()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(args.output)}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
