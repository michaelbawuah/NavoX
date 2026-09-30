"""Development-only measurement of material claim extraction over stored claims.

The measurement compares labelled material-claim spans with spans produced by the
records the application already admitted. It reuses the cluster evaluation
provenance contract and never activates extraction, writes a row, calls a provider
or claims a measured news accuracy corpus.
"""

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.news import NewsClaim, NewsClaimEvidence
from navox.news.cluster_evaluation import CorpusProvenance
from navox.news.clustering import digest_text, normalized_text
from navox.news.contracts import Contract, NewsError, SourceDefinition
from navox.news.evidence import ClaimSpan, permitted_summary_item, quote_at
from navox.news.stories import owned_story

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_STORY_BATCH = 12
MAX_CLAIMS_PER_STORY = 50
PRECISION_TARGET = 0.95
RECALL_TARGET = 0.90
SpanField = Literal["headline", "description"]


def identity_key(item_id: UUID | None, reference: str | None) -> str:
    return f"item:{item_id}" if item_id is not None else f"reference:{reference}"


def span_digest(quote: str) -> str:
    """Digest the same normalized text an admitted claim records."""
    return digest_text(normalized_text(quote))


class SpanReference(Contract):
    """One exact text span in a stored item or captured corpus item."""

    item_id: UUID | None = None
    corpus_item_reference: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    field: SpanField
    start: int = Field(ge=0, le=4000)
    end: int = Field(gt=0, le=4000)
    text_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def bounded_span(self) -> "SpanReference":
        if self.end <= self.start:
            raise ValueError("A span needs a positive length")
        if (self.item_id is None) == (self.corpus_item_reference is None):
            raise ValueError("A span names exactly one stored item or corpus item reference")
        return self

    @property
    def identity(self) -> str:
        return identity_key(self.item_id, self.corpus_item_reference)


class LabeledSpan(SpanReference):
    """A span the label authority recorded as a material claim."""


class ObservedSpan(SpanReference):
    """A span produced by currently admitted stored claims."""


class ExtractionExample(Contract):
    """One captured item, its provenance, its split and its labelled claim spans."""

    item_id: UUID | None = None
    corpus_item_reference: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    provenance: CorpusProvenance
    split: Literal["development", "holdout"]
    labels: tuple[LabeledSpan, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def consistent_identity(self) -> "ExtractionExample":
        if (self.item_id is None) == (self.corpus_item_reference is None):
            raise ValueError("An example names exactly one stored item or corpus item reference")
        owner = (self.item_id, self.corpus_item_reference)
        for label in self.labels:
            if (label.item_id, label.corpus_item_reference) != owner:
                raise ValueError("A label belongs to its own example item")
        return self

    @property
    def identity(self) -> str:
        return identity_key(self.item_id, self.corpus_item_reference)


class ExtractionCorpus(Contract):
    """Captured evaluation items with authored, machine or reviewer labels."""

    reference: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    extraction_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    provenance: CorpusProvenance
    examples: tuple[ExtractionExample, ...] = Field(min_length=4, max_length=5000)

    @model_validator(mode="after")
    def disjoint_splits(self) -> "ExtractionCorpus":
        labelled = any(example.labels for example in self.examples)
        items: dict[str, str] = {}
        texts: dict[str, str] = {}
        for example in self.examples:
            if items.setdefault(example.identity, example.split) != example.split:
                raise ValueError("Item leakage across splits")
            digest = example.provenance.content_digest
            if texts.setdefault(digest, example.split) != example.split:
                raise ValueError("Duplicate captured text leakage across splits")
        for split in ("development", "holdout"):
            rows = [row for row in self.examples if row.split == split]
            if not rows:
                raise ValueError("Each split needs captured examples")
            if labelled and not (
                any(row.labels for row in rows) and any(not row.labels for row in rows)
            ):
                raise ValueError("Each split needs positive and negative extraction examples")
        return self


class ExtractionBundle(Contract):
    """Measurement input file: a captured corpus plus its recorded observed spans."""

    corpus: ExtractionCorpus
    observed: tuple[ObservedSpan, ...] = Field(default=(), max_length=10000)


class ExtractionMetrics(Contract):
    total_examples: int
    labelled_spans: int
    admitted_spans: int
    exact_span_matches: int
    overlapping_span_matches: int
    matched_admitted_spans: int
    unmatched_labels: int
    spans_admitted_outside_labels: int
    abstentions: int
    precision: float | None
    recall: float | None
    exact_span_match_rate: float | None
    measured: bool
    unmeasured_reason: str | None
    observed_targets_met: bool


def _overlap(label: SpanReference, row: SpanReference) -> bool:
    return (
        label.identity == row.identity
        and label.field == row.field
        and label.start < row.end
        and row.start < label.end
    )


def _exact_match(label: SpanReference, row: SpanReference) -> bool:
    return _overlap(label, row) and (label.start, label.end, label.text_digest) == (
        row.start,
        row.end,
        row.text_digest,
    )


def measure(
    examples: tuple[ExtractionExample, ...], observed: tuple[ObservedSpan, ...]
) -> ExtractionMetrics:
    """Compare admitted spans with labels. Precision is unknown, never perfect, if empty."""
    if not examples:
        raise ValueError("An empty sample cannot establish claim extraction quality")
    admitted: dict[str, list[ObservedSpan]] = {example.identity: [] for example in examples}
    for row in observed:
        bucket = admitted.get(row.identity)
        if bucket is None:
            raise ValueError("An observed span belongs to an item outside this sample")
        bucket.append(row)
    labels = [label for example in examples for label in example.labels]
    exact = overlapped = supported = outside = 0
    for label in labels:
        rows = admitted[label.identity]
        if any(_exact_match(label, row) for row in rows):
            exact += 1
        if any(_overlap(label, row) for row in rows):
            overlapped += 1
    for row in observed:
        matching = [label for label in labels if label.identity == row.identity]
        if any(_exact_match(label, row) for label in matching):
            supported += 1
        elif not any(_overlap(label, row) for label in matching):
            outside += 1
    measured = bool(labels)
    precision = supported / len(observed) if observed else None
    recall = exact / len(labels) if measured else None
    rate = exact / overlapped if overlapped else None
    met = bool(
        measured
        and precision is not None
        and precision >= PRECISION_TARGET
        and recall is not None
        and recall >= RECALL_TARGET
        and any(not example.labels for example in examples)
    )
    return ExtractionMetrics(
        total_examples=len(examples),
        labelled_spans=len(labels),
        admitted_spans=len(observed),
        exact_span_matches=exact,
        overlapping_span_matches=overlapped,
        matched_admitted_spans=supported,
        unmatched_labels=len(labels) - overlapped,
        spans_admitted_outside_labels=outside,
        abstentions=sum(1 for example in examples if not admitted[example.identity]),
        precision=precision,
        recall=recall,
        exact_span_match_rate=rate,
        measured=measured,
        unmeasured_reason=(
            None if measured else "The corpus carries no labelled material claim spans"
        ),
        observed_targets_met=met,
    )


class ExtractionReport(Contract):
    corpus_sha256: str
    provenance: CorpusProvenance
    extraction_version: str
    development: ExtractionMetrics
    holdout: ExtractionMetrics
    # Authored fixtures and observed spans cannot authorize extraction activation.
    production_qualified: Literal[False] = False
    runtime_activation: Literal[False] = False


def fingerprint(corpus: ExtractionCorpus) -> str:
    body = json.dumps(corpus.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _split_observed(
    examples: tuple[ExtractionExample, ...], observed: tuple[ObservedSpan, ...]
) -> tuple[ObservedSpan, ...]:
    identities = {example.identity for example in examples}
    return tuple(row for row in observed if row.identity in identities)


def evaluate(corpus: ExtractionCorpus, observed: tuple[ObservedSpan, ...]) -> ExtractionReport:
    corpus = ExtractionCorpus.model_validate(corpus)
    observed = tuple(ObservedSpan.model_validate(row) for row in observed)
    known = {example.identity for example in corpus.examples}
    if any(row.identity not in known for row in observed):
        raise ValueError("Observed spans name items outside the corpus")
    development = tuple(row for row in corpus.examples if row.split == "development")
    holdout = tuple(row for row in corpus.examples if row.split == "holdout")
    return ExtractionReport(
        corpus_sha256=fingerprint(corpus),
        provenance=corpus.provenance,
        extraction_version=corpus.extraction_version,
        development=measure(development, _split_observed(development, observed)),
        holdout=measure(holdout, _split_observed(holdout, observed)),
    )


async def observed_claim_spans(
    database: AsyncSession,
    story_ids: Sequence[UUID],
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    limit_per_story: int = MAX_CLAIMS_PER_STORY,
) -> tuple[ObservedSpan, ...]:
    """Read currently admitted claims as observed spans; nothing is written or generated."""
    if not story_ids or len(story_ids) > MAX_STORY_BATCH:
        raise ValueError("A bounded non-empty story batch is required")
    if not 1 <= limit_per_story <= MAX_CLAIMS_PER_STORY:
        raise ValueError("The per-story claim limit is bounded")
    collected: dict[tuple[str, str, int, int, str], ObservedSpan] = {}
    for story_id in story_ids:
        try:
            story = await owned_story(
                database, story_id, workspace_id=workspace_id, user_id=user_id
            )
        except NewsError:
            continue
        claims = await database.scalars(
            select(NewsClaim)
            .where(
                NewsClaim.cluster_id == story.id,
                NewsClaim.workspace_id == workspace_id,
                NewsClaim.user_id == user_id,
            )
            .order_by(NewsClaim.first_seen_at, NewsClaim.id)
            .limit(limit_per_story)
        )
        for claim in claims:
            try:
                item, view, _ = await permitted_summary_item(
                    database,
                    claim.origin_item_id,
                    definitions,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    now=now,
                )
            except NewsError:
                continue
            if item.revision != claim.origin_revision:
                continue
            evidence = await database.scalar(
                select(NewsClaimEvidence).where(
                    NewsClaimEvidence.claim_id == claim.id,
                    NewsClaimEvidence.news_item_id == claim.origin_item_id,
                )
            )
            if evidence is None:
                continue
            try:
                span = ClaimSpan.model_validate(
                    dict(
                        item_id=item.id,
                        item_revision=item.revision,
                        field=evidence.quote_field,
                        start=evidence.quote_start,
                        end=evidence.quote_end,
                    )
                )
                quote = quote_at(view, span)
            except (NewsError, ValueError):
                continue
            row = ObservedSpan(
                item_id=item.id,
                field=span.field,
                start=span.start,
                end=span.end,
                text_digest=span_digest(quote),
            )
            collected[(row.identity, row.field, row.start, row.end, row.text_digest)] = row
    return tuple(collected[key] for key in sorted(collected))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        with args.input.open("rb") as stream:
            body = stream.read(MAX_INPUT_BYTES + 1)
        if len(body) > MAX_INPUT_BYTES:
            raise ValueError("Corpus is too large")
        bundle = ExtractionBundle.model_validate_json(body)
        report = evaluate(bundle.corpus, bundle.observed)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(report.model_dump_json(indent=2) + "\n")
    except (ValueError, OSError):
        parser.error("Invalid corpus or unavailable output path; existing files were not replaced")
    passed = report.development.observed_targets_met and report.holdout.observed_targets_met
    print("Observed material extraction targets: " + ("PASS" if passed else "FAIL"))
    if not (report.development.measured and report.holdout.measured):
        print("Material extraction measurement: UNMEASURED; no labelled spans are recorded")
    print("Offline authored fixtures only. Production qualification and activation: false.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
