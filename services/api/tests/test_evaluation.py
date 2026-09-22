from pathlib import Path

from navox.evaluation.runner import markdown_report, project_root, run_evaluation


def test_milestone_9_evaluation_gate_passes_reference_snapshot() -> None:
    report = run_evaluation(project_root())

    assert report["passed"] is True
    provider = report["providers"][0]
    assert provider["snapshot_kind"] == "synthetic_reference"
    assert provider["precision"] == 1.0
    assert provider["recall"] == 1.0
    assert provider["schema_valid_rate"] == 1.0
    operational = report["operational_extraction"]
    assert operational["label_accuracy"] == 1.0
    assert operational["schema_valid_rate"] == 1.0
    assert operational["evidence_valid_rate"] == 1.0
    assert operational["adversarial_rejection_rate"] == 1.0
    assert report["security"]["malicious_rejection_rate"] == 1.0
    assert report["security"]["false_positive_suppression_rate"] == 1.0
    assert report["briefing"]["policy_accuracy"] == 1.0
    assert report["planning"]["policy_accuracy"] == 1.0
    assert report["reliability"]["invariant_rate"] == 1.0
    assert all(gate["passed"] for gate in report["gates"].values())


def test_evaluation_report_is_explicit_about_synthetic_reference_data() -> None:
    report = run_evaluation(project_root())
    rendered = markdown_report(report)

    assert "Overall gate: **PASS**" in rendered
    assert "synthetic-reference-v1" in rendered
    assert "SPEC-002 operational extraction" in rendered
    assert "not a claim about a live external model" in rendered


def test_project_root_contains_authoritative_evaluation_contract() -> None:
    root = project_root()

    assert isinstance(root, Path)
    assert (root / "evals/gates.json").is_file()
    assert (root / "evals/security/control_matrix.json").is_file()
    assert (root / "evals/intelligence/extraction_cases.json").is_file()
    assert (root / "evals/intelligence/adversarial_cases.json").is_file()
    assert (root / "evals/intelligence/reference-baseline.json").is_file()
    assert (root / "docs/architecture/SPEC-001-navox.md").is_file()
