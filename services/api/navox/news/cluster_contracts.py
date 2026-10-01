"""Reviewed feature records and the operator deployment policy for semantic joins.

A semantic join is never authorized by a model, a numeric score or a source
statement. Two trusted operator inputs must exist before the pipeline may spend
anything:

* :class:`ReviewedFeatureRecord` -- an internal, source-cited review that binds
  canonical entity, geography and event identities to one exact item revision
  and rights fingerprint, and expires.
* :class:`DeploymentPolicy` -- the operator's deployment decision, bound to one
  exact embedding namespace, source/language scope, corpus manifest digest and
  calibration report digest, with its own expiry.

Nothing in this module installs a policy, promotes a calibration report to
production or qualifies a model or source. Missing, expired, malformed or
non-reviewer-labelled inputs produce ``None`` and the caller performs no paid
work.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from navox.core.settings import Settings
from navox.news.cluster_evaluation import CalibrationReport
from navox.news.clustering import ClusterCalibration
from navox.news.contracts import Contract, SourceDefinition, aware_utc
from navox.news.evidence import ClaimSpan

IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_.:-]{0,127}$"
DIGEST_PATTERN = r"^[0-9a-f]{64}$"
LANGUAGE_PATTERN = r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"

FeatureField = Literal["entity_ids", "geographic_ids", "event_type", "event_identity", "event_time"]
_FEATURE_FIELDS: tuple[FeatureField, ...] = (
    "entity_ids",
    "geographic_ids",
    "event_type",
    "event_identity",
    "event_time",
)

# Independent holdout targets the original specification requires before a
# clustering policy may be deployed. Reported, never assumed.
HOLDOUT_PRECISION_MINIMUM = 0.95
HOLDOUT_RECALL_MINIMUM = 0.90
MAX_POLICY_WINDOW = timedelta(days=90)


def calibration_digest(report: CalibrationReport) -> str:
    """Stable digest of one calibration report, independent of field order."""
    body = json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class FeatureCitation(Contract):
    """One reviewed identity value bound to an exact permitted source span.

    The span is the same claim span this application already validates against
    the current permitted item, so a reviewed identity can never rest on an
    arbitrary reference string: it must quote text that is still stored, still
    permitted and still at the reviewed revision.
    """

    field: FeatureField
    span: ClaimSpan

    @model_validator(mode="after")
    def headline_spans_are_complete(self) -> FeatureCitation:
        if self.span.field == "headline" and self.span.start != 0:
            raise ValueError("A cited headline span must start at the headline")
        return self


class ReviewedFeatureRecord(Contract):
    """A reviewer-attested identity record bound to one exact item revision.

    The reviewer supplies canonical identifiers, never raw model output. Every
    populated signal needs its own citation, so an unsupported identifier cannot
    be smuggled in beside a cited one, and the record cannot outlive its review.
    """

    news_item_id: UUID
    item_revision: int = Field(strict=True, ge=1)
    item_digest: str = Field(pattern=DIGEST_PATTERN)
    rights_fingerprint: str = Field(pattern=DIGEST_PATTERN)
    source_id: UUID
    language: str = Field(pattern=LANGUAGE_PATTERN)
    entity_ids: tuple[str, ...] = Field(default=(), max_length=24)
    geographic_ids: tuple[str, ...] = Field(default=(), max_length=12)
    event_type: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    event_identity: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    event_time: datetime | None = None
    citations: tuple[FeatureCitation, ...] = Field(default=(), max_length=12)
    reviewer: str = Field(min_length=1, max_length=256)
    review_reference: str = Field(min_length=1, max_length=128)
    reviewed_at: datetime
    expires_at: datetime

    _aware = field_validator("reviewed_at", "expires_at")(aware_utc)

    @field_validator("event_time")
    @classmethod
    def aware_event_time(cls, value: datetime | None) -> datetime | None:
        return aware_utc(value) if value is not None else None

    @field_validator("entity_ids", "geographic_ids")
    @classmethod
    def canonical_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("Reviewed identifiers must be unique")
        for value in values:
            if re.fullmatch(IDENTIFIER_PATTERN, value) is None:
                raise ValueError("Reviewed identifiers must be canonical lowercase ids")
        return values

    @model_validator(mode="after")
    def cited_and_expiring(self) -> ReviewedFeatureRecord:
        cited = {citation.field for citation in self.citations}
        if not cited:
            raise ValueError("A reviewed feature record needs at least one citation")
        if not any(getattr(self, name) for name in _FEATURE_FIELDS):
            raise ValueError("A reviewed feature record needs at least one identity signal")
        for name in _FEATURE_FIELDS:
            if getattr(self, name) and name not in cited:
                raise ValueError("Every reviewed signal needs a source citation")
        for citation in self.citations:
            if (
                citation.span.item_id != self.news_item_id
                or citation.span.item_revision != self.item_revision
            ):
                raise ValueError("A reviewed citation must reference this exact item revision")
        if not self.reviewed_at < self.expires_at <= self.reviewed_at + timedelta(days=7):
            raise ValueError("Reviewed features expire within seven days")
        return self


class EmbeddingNamespaceRef(Contract):
    """The pre-call part of the exact embedding namespace.

    The dimension is a property of the qualified model and artifact, so it is
    validated against the returned vector instead of being guessed before the
    call. Every other component is fixed by the operator policy.
    """

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    registry_revision: str = Field(min_length=1, max_length=64)
    artifact: str = Field(min_length=1, max_length=128)
    pipeline_version: str = Field(min_length=1, max_length=64)

    @property
    def digest(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


class EmbeddingNamespace(EmbeddingNamespaceRef):
    """One stored vector namespace, including its observed dimension."""

    dimension: int = Field(strict=True, ge=1, le=4096)

    @property
    def reference(self) -> EmbeddingNamespaceRef:
        return EmbeddingNamespaceRef(
            provider=self.provider,
            model=self.model,
            registry_revision=self.registry_revision,
            artifact=self.artifact,
            pipeline_version=self.pipeline_version,
        )


class DeploymentPolicy(Contract):
    """Operator deployment decision, separate from any calibration report."""

    reference: str = Field(min_length=8, max_length=128)
    deployment_reference: str = Field(min_length=8, max_length=128)
    reviewer: str = Field(min_length=1, max_length=256)
    extractor_version: str = Field(min_length=1, max_length=128)
    namespace: EmbeddingNamespaceRef
    source_keys: tuple[str, ...] = Field(min_length=1, max_length=50)
    language: str = Field(pattern=LANGUAGE_PATTERN)
    corpus_manifest_sha256: str = Field(pattern=DIGEST_PATTERN)
    calibration_report_digest: str = Field(pattern=DIGEST_PATTERN)
    calibration: ClusterCalibration
    minimum_margin: float = Field(ge=0, le=1, strict=True)
    reviewed_at: datetime
    expires_at: datetime

    _aware = field_validator("reviewed_at", "expires_at")(aware_utc)

    @field_validator("source_keys")
    @classmethod
    def unique_sources(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("Deployment policy source scope must be unique")
        return values

    @model_validator(mode="after")
    def bounded_review(self) -> DeploymentPolicy:
        if not self.reviewed_at < self.expires_at <= self.reviewed_at + MAX_POLICY_WINDOW:
            raise ValueError("A deployment policy expires within ninety days")
        if self.calibration.evaluation_reference != self.reference:
            raise ValueError("The policy must bind its own calibration reference")
        return self


class PolicyRejected(ValueError):
    """A fixed reason code; never a source body or provider message."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def policy_digest(policy: DeploymentPolicy) -> str:
    """Immutable identity of one exact reviewed deployment policy.

    A reference is only a label: two policies can share a reference and differ in
    namespace, thresholds or scope, so the proposal must bind this digest.
    """
    body = json.dumps(policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class PolicyStatus:
    """Honest outcome of resolving the operator deployment policy."""

    policy: DeploymentPolicy | None
    reference: str | None
    reason: str | None

    @property
    def active(self) -> bool:
        return self.policy is not None


def validate_deployment_policy(
    *,
    policy: DeploymentPolicy,
    report: CalibrationReport,
    definitions: Mapping[str, SourceDefinition],
    now: datetime,
) -> None:
    """Bind the operator policy to one independently reviewed calibration report.

    Authored or machine-labelled corpora cannot qualify, the report and manifest
    digests must match exactly, the thresholds must be the measured ones, and the
    policy may only name catalog sources of its own reviewed language.
    """
    if (
        report.provenance.label_authority != "reviewer"
        or report.provenance.reviewer_identity != policy.reviewer
    ):
        raise PolicyRejected("authored_corpus")
    if report.corpus_sha256 != policy.corpus_manifest_sha256:
        raise PolicyRejected("manifest_mismatch")
    if calibration_digest(report) != policy.calibration_report_digest:
        raise PolicyRejected("report_mismatch")
    if report.signal_version != policy.extractor_version:
        raise PolicyRejected("extractor_mismatch")
    if (
        report.candidate_policy.calibration != policy.calibration
        or report.candidate_policy.minimum_margin != policy.minimum_margin
    ):
        raise PolicyRejected("threshold_mismatch")
    holdout = report.holdout
    if (
        not report.development.observed_targets_met
        or not holdout.observed_targets_met
        or holdout.precision is None
        or holdout.recall is None
        or holdout.precision < HOLDOUT_PRECISION_MINIMUM
        or holdout.recall < HOLDOUT_RECALL_MINIMUM
    ):
        raise PolicyRejected("holdout_targets")
    if not policy.reviewed_at <= now < policy.expires_at:
        raise PolicyRejected("expired")
    missing = [key for key in policy.source_keys if key not in definitions]
    if missing:
        raise PolicyRejected("source_scope")
    language = policy.language.split("-")[0]
    if any(definitions[key].language.split("-")[0] != language for key in policy.source_keys):
        raise PolicyRejected("source_scope")


def active_policy(
    *,
    settings: Settings,
    definitions: Mapping[str, SourceDefinition],
    now: datetime,
) -> PolicyStatus:
    """The reviewed policy that may be used right now, or an explicit reason."""
    if not settings.news_semantic_clustering_enabled:
        return PolicyStatus(None, None, "disabled")
    raw_policy = settings.news_semantic_deployment_policy
    raw_report = settings.news_semantic_calibration_report
    if raw_policy is None or raw_report is None:
        return PolicyStatus(None, None, "policy_missing")
    try:
        policy = DeploymentPolicy.model_validate(raw_policy)
        report = CalibrationReport.model_validate(raw_report)
    except ValueError:
        return PolicyStatus(None, None, "policy_malformed")
    try:
        validate_deployment_policy(policy=policy, report=report, definitions=definitions, now=now)
    except PolicyRejected as error:
        return PolicyStatus(None, policy.reference, f"policy_invalid:{error.reason}")
    return PolicyStatus(policy, policy.reference, None)


__all__ = [
    "HOLDOUT_PRECISION_MINIMUM",
    "HOLDOUT_RECALL_MINIMUM",
    "MAX_POLICY_WINDOW",
    "DeploymentPolicy",
    "EmbeddingNamespace",
    "EmbeddingNamespaceRef",
    "FeatureCitation",
    "PolicyRejected",
    "PolicyStatus",
    "ReviewedFeatureRecord",
    "active_policy",
    "calibration_digest",
    "policy_digest",
    "validate_deployment_policy",
]
