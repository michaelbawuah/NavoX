import json
from pathlib import Path

import pytest

from navox.evaluation.intelligence_report import build_report


@pytest.mark.parametrize("outcome", ["", "<failure />", "<skipped />", "<error />"])
def test_report_gates_executed_results_not_fixture_accuracy(tmp_path: Path, outcome: str) -> None:
    junit = tmp_path / "results.xml"
    junit.write_text(
        f'<testsuites><testsuite><testcase name="demo">{outcome}</testcase>'
        "</testsuite></testsuites>"
    )
    gates = tmp_path / "gates.json"
    gates.write_text(
        json.dumps(
            {
                "required_tests": ["demo"],
                "minimum_executed_tests": 1,
                "maximum_failed_tests": 0,
                "maximum_skipped_tests": 0,
                "evaluation_kind": "synthetic_application_regression",
                "scope": "synthetic",
            }
        )
    )
    report = build_report(junit, gates)
    assert report["passed"] is (outcome == "")
    assert report["live_provider_quality_measured"] is False
    assert report["production_metrics"] is None


def test_empty_report_cannot_satisfy_required_demos(tmp_path: Path) -> None:
    junit = tmp_path / "results.xml"
    junit.write_text("<testsuites><testsuite /></testsuites>")
    gates = tmp_path / "gates.json"
    gates.write_text(
        json.dumps(
            {
                "required_tests": ["demo"],
                "minimum_executed_tests": 1,
                "maximum_failed_tests": 0,
                "maximum_skipped_tests": 0,
                "evaluation_kind": "synthetic_application_regression",
                "scope": "synthetic",
            }
        )
    )
    report = build_report(junit, gates)
    assert report["passed"] is False
    assert report["required_demos_and_security"]["demo"] == "missing"
