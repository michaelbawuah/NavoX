from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import UniqueConstraint

from navox.agent.contracts import ACTION_CONTRACTS
from navox.agent.planner import MAX_PLAN_STEPS, MAX_REPLANS
from navox.agent.policy import ActionPolicy
from navox.approvals.service import APPROVAL_TTL
from navox.commitments.policy import CommitmentConfidencePolicy
from navox.commitments.schema import CommitmentExtraction
from navox.db.models import (
    Action,
    Approval,
    Commitment,
    IncomingEvent,
    ProactivePreference,
    WorkflowRef,
)
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import OperationalExtraction
from navox.proactive.engine import base_score, signal_type_for, tier_for


@dataclass(frozen=True)
class ProviderMetrics:
    provider: str
    model: str
    snapshot_kind: str
    cases: int
    precision: float
    recall: float
    f1: float
    schema_valid_rate: float
    average_latency_ms: float
    total_estimated_cost_usd: float
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class OperationalExtractionMetrics:
    cases: int
    label_accuracy: float
    schema_valid_rate: float
    evidence_valid_rate: float
    adversarial_rejection_rate: float


@dataclass(frozen=True)
class RateMetric:
    passed: int
    total: int

    @property
    def rate(self) -> float:
        return 1.0 if self.total == 0 else self.passed / self.total


def project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "evals").is_dir() and (parent / "services").is_dir():
            return parent
    raise RuntimeError("Could not locate the NavoX repository root")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def normalized_commitment_key(candidate: dict[str, Any]) -> tuple[str, str]:
    return (
        str(candidate["type"]).strip().casefold(),
        " ".join(str(candidate["title"]).split()).strip().casefold(),
    )


def provider_metrics(
    root: Path,
    *,
    dataset_path: Path,
    snapshot_path: Path,
    policy: CommitmentConfidencePolicy,
) -> ProviderMetrics:
    dataset = load_json(root / dataset_path)
    snapshot = load_json(root / snapshot_path)
    expected_by_case = {
        str(case["id"]): {
            normalized_commitment_key(candidate) for candidate in case.get("expected", [])
        }
        for case in dataset
    }
    outputs = {str(case["case_id"]): case for case in snapshot["cases"]}
    if set(outputs) != set(expected_by_case):
        missing = sorted(set(expected_by_case) - set(outputs))
        unexpected = sorted(set(outputs) - set(expected_by_case))
        raise ValueError(
            f"Provider snapshot case mismatch: missing={missing}, unexpected={unexpected}"
        )

    true_positive = false_positive = false_negative = 0
    schema_valid = 0
    latencies: list[float] = []
    total_cost = 0.0
    input_tokens = output_tokens = 0

    for case_id, expected in expected_by_case.items():
        case = outputs[case_id]
        latencies.append(float(case.get("latency_ms", 0.0)))
        total_cost += float(case.get("estimated_cost_usd", 0.0))
        input_tokens += int(case.get("input_tokens", 0))
        output_tokens += int(case.get("output_tokens", 0))

        predicted: set[tuple[str, str]] = set()
        try:
            extraction = CommitmentExtraction.model_validate(case["output"])
        except ValidationError:
            extraction = None
        if extraction is not None:
            schema_valid += 1
            for candidate in extraction.candidates:
                if policy.status_for(candidate) is not None:
                    predicted.add(
                        (candidate.type.casefold(), " ".join(candidate.title.split()).casefold())
                    )

        true_positive += len(predicted & expected)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)

    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = true_positive / precision_denominator if precision_denominator else 1.0
    recall = true_positive / recall_denominator if recall_denominator else 1.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

    return ProviderMetrics(
        provider=str(snapshot["provider"]),
        model=str(snapshot["model"]),
        snapshot_kind=str(snapshot.get("snapshot_kind", "unknown")),
        cases=len(expected_by_case),
        precision=precision,
        recall=recall,
        f1=f1,
        schema_valid_rate=schema_valid / len(expected_by_case) if expected_by_case else 1.0,
        average_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
        total_estimated_cost_usd=total_cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _evaluation_source_document(case: dict[str, Any], index: int) -> SourceDocument:
    occurred_at = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    return SourceDocument(
        id=UUID(int=10_000 + index),
        workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
        provider="evaluation",
        source_type="synthetic_case",
        external_id=str(case.get("id") or case.get("name") or index),
        subject=str(case["subject"]) if case.get("subject") is not None else None,
        content=str(case["content"]) if case.get("content") is not None else None,
        occurred_at=occurred_at,
        retrieved_at=occurred_at,
    )


def operational_extraction_metrics(root: Path) -> OperationalExtractionMetrics:
    dataset = load_json(root / "evals/intelligence/extraction_cases.json")
    snapshot = load_json(root / "evals/intelligence/reference-baseline.json")
    outputs = {str(case["case_id"]): case["output"] for case in snapshot["cases"]}
    expected_ids = {str(case["id"]) for case in dataset}
    if set(outputs) != expected_ids:
        missing = sorted(expected_ids - set(outputs))
        unexpected = sorted(set(outputs) - expected_ids)
        raise ValueError(
            f"Operational extraction snapshot mismatch: missing={missing}, unexpected={unexpected}"
        )

    schema_valid = evidence_valid = label_correct = 0
    for index, case in enumerate(dataset):
        case_id = str(case["id"])
        try:
            extraction = OperationalExtraction.model_validate(outputs[case_id])
        except ValidationError:
            continue
        schema_valid += 1

        document = _evaluation_source_document(case, index)
        try:
            extraction.validate_evidence(document)
        except ValueError:
            continue
        evidence_valid += 1

        observed_types = {item.observation_type for item in extraction.observations}
        observed_temporals = {item.expression for item in extraction.temporals}
        observed_people = {item.name for item in extraction.people}
        observed_relationships = {item.relationship_type for item in extraction.relationships}
        if (
            observed_types == set(case.get("expected_observation_types", []))
            and observed_temporals == set(case.get("expected_temporal_expressions", []))
            and observed_people == set(case.get("expected_people", []))
            and observed_relationships == set(case.get("expected_relationship_types", []))
        ):
            label_correct += 1

    adversarial = load_json(root / "evals/intelligence/adversarial_cases.json")
    rejected = 0
    for index, case in enumerate(adversarial):
        try:
            extraction = OperationalExtraction.model_validate(case["model_output"])
            extraction.validate_evidence(_evaluation_source_document(case, 1_000 + index))
        except (ValidationError, ValueError):
            rejected += 1

    total = len(dataset)
    return OperationalExtractionMetrics(
        cases=total,
        label_accuracy=label_correct / total if total else 1.0,
        schema_valid_rate=schema_valid / total if total else 1.0,
        evidence_valid_rate=evidence_valid / total if total else 1.0,
        adversarial_rejection_rate=(
            rejected / len(adversarial) if adversarial else 1.0
        ),
    )


def commitment_security_metrics(
    root: Path,
    policy: CommitmentConfidencePolicy,
) -> dict[str, float]:
    malicious = load_json(root / "evals/commitments/malicious_outputs.json")
    rejected = 0
    for case in malicious:
        try:
            CommitmentExtraction.model_validate(case["model_output"])
        except ValidationError:
            rejected += 1

    false_positives = load_json(root / "evals/commitments/false_positive_outputs.json")
    suppressed = 0
    total_false_candidates = 0
    for case in false_positives:
        extraction = CommitmentExtraction.model_validate(case["model_output"])
        for candidate in extraction.candidates:
            total_false_candidates += 1
            if policy.status_for(candidate) is None:
                suppressed += 1

    return {
        "malicious_rejection_rate": rejected / len(malicious) if malicious else 1.0,
        "false_positive_suppression_rate": (
            suppressed / total_false_candidates if total_false_candidates else 1.0
        ),
    }


def briefing_policy_metric(root: Path) -> float:
    cases = load_json(root / "evals/briefing/cases.json")
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    workspace_id = UUID("22222222-2222-4222-8222-222222222222")
    correct = 0

    for index, case in enumerate(cases):
        due_in_hours = case.get("due_in_hours")
        waiting_for_hours = case.get("waiting_for_hours")
        commitment = Commitment(
            id=UUID(int=index + 1),
            user_id=user_id,
            workspace_id=workspace_id,
            commitment_type=case["type"],
            title=case["id"],
            description=None,
            status=case["status"],
            priority=case["priority"],
            due_at=(
                now + timedelta(hours=float(due_in_hours)) if due_in_hours is not None else None
            ),
            confidence=1.0,
            created_by="evaluation",
            dedupe_key=f"{index + 1:064x}",
            valid_from=now - timedelta(days=30),
            valid_until=None,
            waiting_since=(
                now - timedelta(hours=float(waiting_for_hours))
                if waiting_for_hours is not None
                else None
            ),
        )
        preference = ProactivePreference(
            user_id=user_id,
            workspace_id=workspace_id,
            notifications_enabled=bool(case.get("notifications_allowed", True)),
            quiet_hours_start="22:00",
            quiet_hours_end="07:00",
            daily_briefing_hour=8,
            notify_threshold=85,
            briefing_threshold=65,
            dashboard_threshold=40,
            max_interruptions_per_day=3,
            cooldown_minutes=240,
        )
        breakdown = base_score(
            commitment,
            now=now,
            objective_active=bool(case.get("objective_active", False)),
            notification_fatigue=int(case.get("notification_fatigue", 0)),
        )
        tier = tier_for(
            breakdown.score,
            preference,
            quiet=bool(case.get("quiet", False)),
            interruption_budget_exhausted=bool(case.get("interruption_budget_exhausted", False)),
            notifications_allowed=bool(case.get("notifications_allowed", True)),
        )
        if (
            tier == case["expected_tier"]
            and signal_type_for(commitment) == case["expected_signal_type"]
        ):
            correct += 1

    return correct / len(cases) if cases else 1.0


def planning_policy_metric(root: Path) -> float:
    cases = load_json(root / "evals/planning/action_policy_cases.json")
    policy = ActionPolicy()
    correct = 0
    for case in cases:
        contract = ACTION_CONTRACTS[str(case["action"])]
        decision = policy.evaluate(
            contract,
            granted_permissions=set(case.get("granted_permissions", [])),
            agent_paused=bool(case.get("agent_paused", False)),
        )
        expected = case["expected"]
        if (
            decision.allowed is bool(expected["allowed"])
            and decision.requires_approval is bool(expected["requires_approval"])
            and decision.reason == expected["reason"]
        ):
            correct += 1
    return correct / len(cases) if cases else 1.0


def has_unique_constraint(model: type[Any], columns: set[str]) -> bool:
    for constraint in model.__table__.constraints:
        if isinstance(constraint, UniqueConstraint):
            if {column.name for column in constraint.columns} == columns:
                return True
    return False


def reliability_invariant_metric() -> RateMetric:
    gmail_send = ACTION_CONTRACTS["gmail.send"]
    checks = (
        MAX_PLAN_STEPS == 8,
        MAX_REPLANS == 2,
        APPROVAL_TTL == timedelta(minutes=15),
        gmail_send.risk_level == "R3",
        gmail_send.idempotency_policy == "navox_at_most_once_no_ambiguous_retry",
        gmail_send.verification_method == "provider_message_id",
        has_unique_constraint(
            IncomingEvent,
            {"connection_id", "provider", "external_event_id"},
        ),
        has_unique_constraint(Action, {"workspace_id", "idempotency_key"}),
        has_unique_constraint(Approval, {"workspace_id", "decision_request_id"}),
        has_unique_constraint(
            WorkflowRef,
            {"entity_type", "entity_id", "workflow_type"},
        ),
        bool(WorkflowRef.__table__.c.temporal_workflow_id.unique),
    )
    return RateMetric(sum(checks), len(checks))


def security_control_coverage(root: Path) -> RateMetric:
    matrix = load_json(root / "evals/security/control_matrix.json")
    passed = 0
    for control in matrix:
        evidence_ok = True
        evidence = control.get("evidence", [])
        if not evidence:
            evidence_ok = False
        for item in evidence:
            path = root / str(item["path"])
            if not path.is_file():
                evidence_ok = False
                break
            expected_text = str(item.get("contains", ""))
            if expected_text and expected_text not in path.read_text(encoding="utf-8"):
                evidence_ok = False
                break
        if evidence_ok:
            passed += 1
    return RateMetric(passed, len(matrix))


def _gate_result(value: float, minimum: float) -> dict[str, object]:
    return {
        "value": round(value, 6),
        "minimum": minimum,
        "passed": value >= minimum,
    }


def run_evaluation(root: Path | None = None) -> dict[str, Any]:
    repository_root = root or project_root()
    gates = load_json(repository_root / "evals/gates.json")
    policy = CommitmentConfidencePolicy(moderate_threshold=0.65, high_threshold=0.85)

    provider_paths = sorted((repository_root / "evals/providers").glob("*.json"))
    providers = [
        provider_metrics(
            repository_root,
            dataset_path=Path("evals/commitments/labeled_cases.json"),
            snapshot_path=path.relative_to(repository_root),
            policy=policy,
        )
        for path in provider_paths
    ]
    if not providers:
        raise ValueError("At least one provider evaluation snapshot is required")

    primary = providers[0]
    operational_extraction = operational_extraction_metrics(repository_root)
    security = commitment_security_metrics(repository_root, policy)
    briefing_accuracy = briefing_policy_metric(repository_root)
    planning_accuracy = planning_policy_metric(repository_root)
    reliability = reliability_invariant_metric()
    security_coverage = security_control_coverage(repository_root)

    observed = {
        "commitment_precision": primary.precision,
        "commitment_recall": primary.recall,
        "structured_output_rate": primary.schema_valid_rate,
        "operational_extraction_label_accuracy": operational_extraction.label_accuracy,
        "operational_extraction_schema_valid_rate": operational_extraction.schema_valid_rate,
        "operational_extraction_evidence_valid_rate": operational_extraction.evidence_valid_rate,
        "operational_extraction_adversarial_rejection_rate": (
            operational_extraction.adversarial_rejection_rate
        ),
        **security,
        "briefing_policy_accuracy": briefing_accuracy,
        "planning_policy_accuracy": planning_accuracy,
        "security_control_coverage": security_coverage.rate,
        "reliability_invariant_rate": reliability.rate,
    }
    gate_results = {
        name: _gate_result(float(observed[name]), float(minimum)) for name, minimum in gates.items()
    }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": "navox-m9-synthetic-v1",
        "providers": [asdict(metrics) for metrics in providers],
        "operational_extraction": asdict(operational_extraction),
        "security": {
            **security,
            "control_coverage": {
                "passed": security_coverage.passed,
                "total": security_coverage.total,
                "rate": security_coverage.rate,
            },
        },
        "briefing": {"policy_accuracy": briefing_accuracy},
        "planning": {"policy_accuracy": planning_accuracy},
        "reliability": {
            "invariants_passed": reliability.passed,
            "invariants_total": reliability.total,
            "invariant_rate": reliability.rate,
        },
        "gates": gate_results,
        "passed": all(bool(result["passed"]) for result in gate_results.values()),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# NavoX Evaluation Report",
        "",
        f"- Dataset: `{report['dataset']}`",
        f"- Overall gate: **{'PASS' if report['passed'] else 'FAIL'}**",
        "",
        "## Provider snapshots",
        "",
        "| Provider | Model | Kind | Precision | Recall | F1 | Schema | Avg latency | Cost |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for provider in report["providers"]:
        lines.append(
            "| {provider} | {model} | {kind} | {precision:.3f} | {recall:.3f} | "
            "{f1:.3f} | {schema:.3f} | {latency:.1f} ms | ${cost:.6f} |".format(
                provider=provider["provider"],
                model=provider["model"],
                kind=provider["snapshot_kind"],
                precision=provider["precision"],
                recall=provider["recall"],
                f1=provider["f1"],
                schema=provider["schema_valid_rate"],
                latency=provider["average_latency_ms"],
                cost=provider["total_estimated_cost_usd"],
            )
        )
    operational = report["operational_extraction"]
    lines.extend(
        [
            "",
            "## SPEC-002 operational extraction",
            "",
            f"- Label accuracy: {operational['label_accuracy']:.3f}",
            f"- Schema-valid rate: {operational['schema_valid_rate']:.3f}",
            f"- Evidence-valid rate: {operational['evidence_valid_rate']:.3f}",
            f"- Adversarial rejection rate: {operational['adversarial_rejection_rate']:.3f}",
            "",
            "## Release gates",
            "",
            "| Gate | Value | Minimum | Result |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for name, gate in report["gates"].items():
        lines.append(
            f"| {name} | {gate['value']:.3f} | {gate['minimum']:.3f} | "
            f"{'PASS' if gate['passed'] else 'FAIL'} |"
        )
    lines.append("")
    lines.append(
        "_The committed reference snapshot is synthetic evaluation data, not a claim about "
        "a live external model. Live provider snapshots must identify their provider/model and "
        "use the same labeled dataset contract._"
    )
    lines.append("")
    return "\n".join(lines)
