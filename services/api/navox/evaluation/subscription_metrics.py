"""Measure bounded labeled SPEC-004 documents and require its safety regressions."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field

from navox.evaluation.connector_metrics import source_revision
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.subscriptions.discovery import extract_evidence

TARGETS = {
    "discovery_precision": (">=", 0.95),
    "discovery_recall": (">=", 0.90),
    "marketing_false_positive_rate": ("<=", 0.02),
    "merchant_name_accuracy": (">=", 0.97),
    "amount_accuracy": (">=", 0.98),
    "currency_accuracy": (">=", 0.99),
    "interval_accuracy": (">=", 0.97),
    "explicit_renewal_accuracy": (">=", 0.98),
    "trial_detection": (">=", 0.95),
    "price_change_detection": (">=", 0.95),
    "cancellation_confirmation_detection": (">=", 0.98),
}
REQUIRED_CHECKS = (
    "test_complete_r4_cancellation_has_independent_evidence",
    "test_prepare_alone_and_generic_approval_cannot_cancel",
    "test_replayed_confirmation_is_rejected",
    "test_provider_supported_cancellation_executes_then_independently_verifies",
    "test_external_authority_and_urls_are_never_interpreted_as_capabilities",
    "test_manual_trial_reaches_existing_today_and_keep_suppresses",
    "test_deleting_only_source_erases_all_derived_registry_and_attention",
    "test_foreign_subscription_edge_blocks_source_erasure",
    "test_partial_correction_cannot_preserve_source_snapshot_without_evidence",
    "test_prevented_renewals_requires_confirmed_independently_verified_future_cycle",
)


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    split: str
    content: str
    positive: bool
    type: str | None = None
    merchant: str = "Acme"
    amount: str | None = None
    currency: str | None = None
    interval: str | None = None
    renewal: str | None = None
    trial: str | None = None
    count: int = 1
    marketing: bool = False
    uncertain: bool = False


class Corpus(BaseModel):
    scope: str
    examples: list[Example] = Field(min_length=1)


def measure_split(examples: list[Example]) -> dict[str, object]:
    counts: Counter[str] = Counter()
    now = datetime(2026, 8, 1, tzinfo=UTC)
    for case in examples:
        candidate = extract_evidence(
            SourceDocument(
                id=uuid5(NAMESPACE_URL, case.id),
                workspace_id=uuid5(NAMESPACE_URL, "spec004-eval"),
                provider="google",
                source_type="email",
                external_id=case.id,
                subject="Account notice",
                content=case.content,
                author=SourceIdentity(
                    identity_type="email",
                    identity_value="billing@example.net",
                    display_name=case.merchant,
                ),
                occurred_at=now,
                retrieved_at=now,
            )
        )
        counts["positive"] += case.positive
        counts["predicted"] += candidate is not None
        counts["tp"] += case.positive and candidate is not None
        counts["marketing"] += case.marketing
        counts["marketing_fp"] += case.marketing and candidate is not None
        if case.positive:
            counts["merchant_name_accuracy_denominator"] += 1
            counts["merchant_name_accuracy_numerator"] += (
                candidate is not None and candidate.merchant_text == case.merchant
            )
        labels = {
            "amount_accuracy": (case.amount, str(candidate.amount) if candidate else None),
            "currency_accuracy": (case.currency, candidate.currency if candidate else None),
            "interval_accuracy": (case.interval, candidate.billing_interval if candidate else None),
            "explicit_renewal_accuracy": (
                case.renewal,
                candidate.renewal_at.date().isoformat()
                if candidate and candidate.renewal_at
                else None,
            ),
            "trial_detection": (
                case.trial,
                candidate.trial_ends_at.date().isoformat()
                if candidate and candidate.trial_ends_at
                else None,
            ),
            "price_change_detection": (
                case.type if case.type == "PRICE_CHANGE" else None,
                candidate.evidence_type if candidate else None,
            ),
            "cancellation_confirmation_detection": (
                case.type if case.type == "CANCELLATION_CONFIRMATION" else None,
                candidate.evidence_type if candidate else None,
            ),
        }
        for metric, (expected, actual) in labels.items():
            if expected is not None:
                counts[f"{metric}_denominator"] += 1
                equal = expected == actual
                if metric == "amount_accuracy" and actual is not None and actual != "None":
                    equal = Decimal(expected) == Decimal(actual)
                if metric == "interval_accuracy" and candidate:
                    equal &= case.count == candidate.interval_count
                counts[f"{metric}_numerator"] += equal
    counts.update(
        {
            "discovery_precision_numerator": counts["tp"],
            "discovery_precision_denominator": counts["predicted"],
            "discovery_recall_numerator": counts["tp"],
            "discovery_recall_denominator": counts["positive"],
            "marketing_false_positive_rate_numerator": counts["marketing_fp"],
            "marketing_false_positive_rate_denominator": counts["marketing"],
        }
    )
    metrics: dict[str, object] = {}
    outcomes = []
    for metric, (operator, threshold) in TARGETS.items():
        numerator, denominator = counts[f"{metric}_numerator"], counts[f"{metric}_denominator"]
        rate = numerator / denominator if denominator else None
        passed = rate is not None and (rate >= threshold if operator == ">=" else rate <= threshold)
        outcomes.append(passed)
        metrics[metric] = {
            "numerator": numerator,
            "denominator": denominator,
            "rate": rate,
            "target": f"{operator} {threshold}",
            "passed": passed,
        }
    return {"documents": len(examples), "metrics": metrics, "passed": all(outcomes)}


def build_report(corpus_path: Path, junit: Path) -> dict[str, object]:
    corpus = Corpus.model_validate_json(corpus_path.read_text())
    if len({case.id for case in corpus.examples}) != len(corpus.examples):
        raise ValueError("Duplicate labeled document IDs")
    splits = {
        name: measure_split([case for case in corpus.examples if case.split == name])
        for name in ("development", "holdout")
    }
    tree = ElementTree.parse(junit)
    cases = list(tree.iter("testcase"))
    successful = {
        case.get("name")
        for case in cases
        if not any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
    }
    checks = {name: name in successful for name in REQUIRED_CHECKS}
    failed = sum(
        any(case.find(tag) is not None for tag in ("failure", "error", "skipped")) for case in cases
    )
    return {
        "schema_version": "spec-004-measurements.v1",
        "scope": corpus.scope,
        "generated_at": datetime.now(UTC).isoformat(),
        "splits": splits,
        "required_regressions": checks,
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "junit_sha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
        "source": source_revision(),
        "passed": all(split["passed"] for split in splits.values())
        and all(checks.values())
        and failed == 0,
        "production_accuracy": None,
        "limitations": [
            "Synthetic samples provide regression evidence, not live-mail precision estimates.",
            "Merchant name labels do not measure semantic alias resolution in production.",
            "Safety and deduplication tests are required checks, not inferred operation rates.",
            "The real Temporal/PostgreSQL disposable-provider demo is a separate Compose gate.",
            "Real cancellation needs a reviewed provider profile, owner grant and exact approval.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "tests/fixtures/subscriptions/discovery.json",
    )
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.corpus, args.junit)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "report": str(args.output)}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
