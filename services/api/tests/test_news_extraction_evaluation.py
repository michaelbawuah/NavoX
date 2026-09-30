"""Synthetic material-extraction regressions, never a measured news accuracy corpus."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_news_foundation import NOW as FOUNDATION_NOW
from test_news_foundation import (
    OTHER_USER,
    OTHER_WORKSPACE,
    USER,
    WORKSPACE,
    definition,
    seed,
)

from navox.db.news import NewsClaim
from navox.news.contracts import NewsItemInput
from navox.news.evidence import ClaimSpan, admit_claim
from navox.news.extraction_evaluation import (
    CorpusProvenance,
    ExtractionBundle,
    ExtractionCorpus,
    ExtractionExample,
    LabeledSpan,
    ObservedSpan,
    evaluate,
    fingerprint,
    measure,
    observed_claim_spans,
    span_digest,
)
from navox.news.ingestion import store_item
from navox.news.registry import activate_source, current_rights, revoke_rights
from navox.news.stories import index_item

FIXTURES = Path(__file__).parent / "fixtures" / "spec006"
MEASURED_FIXTURE = FIXTURES / "extraction_corpus.json"
UNMEASURED_FIXTURE = FIXTURES / "extraction_corpus_unmeasured.json"
NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
FIELD = "description"
RIGHTS = "authored synthetic fixture; no publisher text is captured or redistributed"
# The authored fixture text behind the corpus digests; nothing here is publisher content.
FIXTURE_TEXT = {
    "11111111-1111-4111-8111-111111111111": "Synthetic gauge nine recorded a peak at 0412.",
    "22222222-2222-4222-8222-222222222222": "The fixture control room logged two manual resets.",
    "33333333-3333-4333-8333-333333333333": "A synthetic item carries no labelled material claim.",
    "44444444-4444-4444-8444-444444444444": "Synthetic ballast reads nominal in the holdout item.",
    "55555555-5555-4555-8555-555555555555": "This holdout item carries no labelled claim either.",
}


def provenance(number: int, *, authority: str = "authored", reviewer: str | None = None):
    return CorpusProvenance(
        source_identity=f"authored-fixture-{number}",
        captured_at=NOW,
        rights_basis=RIGHTS,
        label_authority=authority,
        reviewer_identity=reviewer,
        content_digest=f"{number:064x}",
    )


def text_for(number: int) -> str:
    return f"Fixture observation number {number} was recorded."


def identifier(number: int) -> UUID:
    return UUID(int=number * 10 + 1)


def bound(number: int, *, start: int = 0, end: int | None = None) -> tuple[int, int]:
    body = text_for(number)
    return start, len(body) if end is None else end


def label(number: int, *, start: int = 0, end: int | None = None) -> LabeledSpan:
    body = text_for(number)
    first, last = bound(number, start=start, end=end)
    return LabeledSpan(
        item_id=identifier(number),
        field=FIELD,
        start=first,
        end=last,
        text_digest=span_digest(body[first:last]),
    )


def observed(number: int, *, start: int = 0, end: int | None = None) -> ObservedSpan:
    body = text_for(number)
    first, last = bound(number, start=start, end=end)
    return ObservedSpan(
        item_id=identifier(number),
        field=FIELD,
        start=first,
        end=last,
        text_digest=span_digest(body[first:last]),
    )


def build(number: int, split: str, *, labelled: bool) -> ExtractionExample:
    return ExtractionExample(
        item_id=identifier(number),
        provenance=provenance(number),
        split=split,
        labels=(label(number),) if labelled else (),
    )


def sample(*, labelled: bool = True) -> tuple[ExtractionExample, ExtractionExample]:
    return (build(1, "development", labelled=labelled), build(2, "development", labelled=False))


def corpus() -> ExtractionCorpus:
    return ExtractionCorpus(
        reference="synthetic-extraction-regression-only",
        extraction_version="fixture-v1",
        provenance=provenance(0),
        examples=(
            build(1, "development", labelled=True),
            build(2, "development", labelled=False),
            build(3, "holdout", labelled=True),
            build(4, "holdout", labelled=False),
        ),
    )


def test_exact_span_is_a_complete_match():
    metrics = measure(sample(), (observed(1),))
    assert metrics.total_examples == 2 and metrics.labelled_spans == 1
    assert metrics.admitted_spans == 1 and metrics.exact_span_matches == 1
    assert metrics.matched_admitted_spans == 1 and metrics.overlapping_span_matches == 1
    assert metrics.precision == 1.0 and metrics.recall == 1.0
    assert metrics.exact_span_match_rate == 1.0
    assert metrics.unmatched_labels == 0 and metrics.spans_admitted_outside_labels == 0
    assert metrics.abstentions == 1 and metrics.observed_targets_met
    assert metrics.measured and metrics.unmeasured_reason is None


def test_partial_overlap_is_never_an_exact_match():
    metrics = measure(sample(), (observed(1, end=len(text_for(1)) - 1),))
    assert metrics.overlapping_span_matches == 1 and metrics.exact_span_matches == 0
    assert metrics.exact_span_match_rate == 0.0 and metrics.unmatched_labels == 0
    assert metrics.precision == 0.0 and metrics.recall == 0.0
    assert not metrics.observed_targets_met


def test_admitted_span_outside_any_label_lowers_precision():
    metrics = measure(sample(), (observed(1), observed(2, end=10)))
    assert metrics.admitted_spans == 2 and metrics.matched_admitted_spans == 1
    assert metrics.spans_admitted_outside_labels == 1
    assert metrics.precision == 0.5 and metrics.recall == 1.0


def test_missing_label_counts_against_recall():
    metrics = measure(sample(), (observed(2, end=10),))
    assert metrics.labelled_spans == 1 and metrics.admitted_spans == 1
    assert metrics.unmatched_labels == 1 and metrics.exact_span_matches == 0
    assert metrics.recall == 0.0 and metrics.precision == 0.0
    assert not metrics.observed_targets_met


def test_nothing_admitted_is_unmeasured_precision_not_perfect():
    metrics = measure(sample(), ())
    assert metrics.admitted_spans == 0 and metrics.precision is None
    assert metrics.recall == 0.0 and metrics.exact_span_match_rate is None
    assert metrics.measured and metrics.abstentions == 2
    assert not metrics.observed_targets_met
    with pytest.raises(ValueError):
        measure((), ())


def test_observed_span_for_a_foreign_item_is_rejected():
    with pytest.raises(ValueError):
        measure((build(1, "development", labelled=True),), (observed(9),))


def test_corpus_without_labels_reports_an_explicit_unmeasured_state():
    body = corpus().model_dump(mode="json")
    for row in body["examples"]:
        row["labels"] = []
    blank = ExtractionCorpus.model_validate(body)
    report = evaluate(blank, ())
    for metrics in (report.development, report.holdout):
        assert not metrics.measured and metrics.unmeasured_reason is not None
        assert metrics.precision is None and metrics.recall is None
        assert metrics.exact_span_match_rate is None
        assert not metrics.observed_targets_met
    assert not report.production_qualified and not report.runtime_activation


def test_corpus_requires_provenance():
    body = corpus().model_dump(mode="json")
    del body["provenance"]
    with pytest.raises(ValidationError):
        ExtractionCorpus.model_validate(body)


def test_reviewer_labels_need_a_recorded_reviewer_identity():
    body = corpus().model_dump(mode="json")
    body["provenance"]["label_authority"] = "reviewer"
    with pytest.raises(ValidationError):
        ExtractionCorpus.model_validate(body)
    body["provenance"]["reviewer_identity"] = "named-fixture-reviewer"
    assert (
        ExtractionCorpus.model_validate(body).provenance.reviewer_identity
        == "named-fixture-reviewer"
    )
    for authority in ("authored", "machine"):
        body["provenance"]["label_authority"] = authority
        with pytest.raises(ValidationError):
            ExtractionCorpus.model_validate(body)


@pytest.mark.parametrize(
    "kind",
    ["split", "duplicate-text", "no-positive", "no-negative", "foreign-label", "two-identities"],
)
def test_corpus_leakage_and_label_integrity(kind):
    body = corpus().model_dump(mode="json")
    rows = body["examples"]
    if kind == "split":
        rows[2]["item_id"] = rows[0]["item_id"]
        rows[2]["labels"][0]["item_id"] = rows[0]["item_id"]
    if kind == "duplicate-text":
        rows[2]["provenance"]["content_digest"] = rows[0]["provenance"]["content_digest"]
    if kind == "no-positive":
        rows[0]["labels"] = []
    if kind == "no-negative":
        moved = json.loads(json.dumps(rows[0]["labels"][0]))
        moved["item_id"] = rows[1]["item_id"]
        rows[1]["labels"] = [moved]
    if kind == "foreign-label":
        rows[0]["labels"][0]["item_id"] = rows[2]["item_id"]
    if kind == "two-identities":
        rows[0]["corpus_item_reference"] = "authored-corpus-item-1"
    with pytest.raises(ValidationError):
        ExtractionCorpus.model_validate(body)


def test_report_is_byte_stable_and_retains_provenance():
    bundle = evaluate(corpus(), (observed(1), observed(3)))
    repeat = evaluate(corpus(), (observed(1), observed(3)))
    assert bundle.model_dump_json() == repeat.model_dump_json()
    assert bundle.corpus_sha256 == fingerprint(corpus()) == repeat.corpus_sha256
    assert bundle.provenance == corpus().provenance
    assert bundle.provenance.label_authority == "authored"
    assert bundle.provenance.reviewer_identity is None
    assert not bundle.production_qualified and not bundle.runtime_activation


def test_fixture_corpus_is_measured_and_within_targets():
    document = json.loads(MEASURED_FIXTURE.read_text())
    bundle = ExtractionBundle.model_validate(document)
    report = evaluate(bundle.corpus, bundle.observed)
    assert report.provenance.label_authority == "authored"
    assert report.provenance.reviewer_identity is None
    for metrics in (report.development, report.holdout):
        assert metrics.measured and metrics.observed_targets_met
        assert metrics.precision == 1.0 and metrics.recall == 1.0
        assert metrics.spans_admitted_outside_labels == 0
    assert not report.production_qualified and not report.runtime_activation


def test_fixture_digests_match_the_authored_fixture_text():
    document = json.loads(MEASURED_FIXTURE.read_text())
    for example in document["corpus"]["examples"]:
        body = FIXTURE_TEXT[example["item_id"]]
        assert example["provenance"]["content_digest"] == span_digest(body)
        for label in example["labels"]:
            assert label["text_digest"] == span_digest(body[label["start"] : label["end"]].strip())
    for row in document["observed"]:
        body = FIXTURE_TEXT[row["item_id"]]
        assert row["text_digest"] == span_digest(body[row["start"] : row["end"]].strip())


def run_cli(source: Path, output: Path):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "navox.news.extraction_evaluation",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )


def test_cli_writes_private_report_without_overwriting(tmp_path):
    source, output = tmp_path / "corpus.json", tmp_path / "report.json"
    source.write_bytes(MEASURED_FIXTURE.read_bytes())
    result = run_cli(source, output)
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout and "UNMEASURED" not in result.stdout
    saved = output.read_bytes()
    report = json.loads(saved)
    assert not report["production_qualified"] and not report["runtime_activation"]
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    assert run_cli(source, output).returncode != 0
    assert output.read_bytes() == saved


def test_cli_reports_the_unmeasured_state(tmp_path):
    source, output = tmp_path / "corpus.json", tmp_path / "report.json"
    source.write_bytes(UNMEASURED_FIXTURE.read_bytes())
    result = run_cli(source, output)
    assert result.returncode != 0
    assert "UNMEASURED" in result.stdout and "PASS" not in result.stdout
    report = json.loads(output.read_bytes())
    assert report["development"]["measured"] is False
    assert report["development"]["precision"] is None
    assert not report["production_qualified"]


async def item(db, key: str = "adapter-fixture"):
    config = definition(summary_generation_allowed=True).model_copy(
        update={
            "key": f"source-{key}",
            "name": f"Source {key}",
            "domain": f"{key}.example.com",
            "endpoint": f"https://{key}.example.com/feed",
            "article_domains": (f"{key}.example.com",),
            "independence_group": f"origin-{key}",
        }
    )
    source = await activate_source(
        db, config, workspace_id=WORKSPACE, user_id=USER, now=FOUNDATION_NOW
    )
    row = await store_item(
        db,
        source,
        await current_rights(db, source),
        config,
        NewsItemInput(
            external_id=key,
            headline="An observation was recorded",
            description="The instrument measured a change.",
            canonical_url=f"https://{key}.example.com/report",
            published_at=FOUNDATION_NOW,
            categories=("science",),
        ),
        now=FOUNDATION_NOW,
    )
    return config, source, row


@pytest.mark.asyncio
async def test_adapter_reads_only_currently_permitted_exact_spans(ai_database):
    await seed(ai_database)
    async with ai_database() as database:
        config, source, row = await item(database)
        definitions = {config.key: config}
        story = await index_item(database, row, definitions, now=FOUNDATION_NOW)
        span = ClaimSpan(
            item_id=row.id,
            item_revision=row.revision,
            field="headline",
            start=0,
            end=len(row.headline),
        )
        claim = await admit_claim(database, story, span, definitions, now=FOUNDATION_NOW)
        observed = await observed_claim_spans(
            database,
            (story.id,),
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            now=FOUNDATION_NOW,
        )
        assert len(observed) == 1
        assert observed[0].item_id == row.id and observed[0].field == "headline"
        assert observed[0].text_digest == span_digest(claim.claim_text)
        assert observed[0].text_digest == claim.text_digest
        assert (
            await observed_claim_spans(
                database,
                (story.id,),
                definitions,
                workspace_id=OTHER_WORKSPACE,
                user_id=OTHER_USER,
                now=FOUNDATION_NOW,
            )
            == ()
        )
        assert await database.scalar(select(NewsClaim).where(NewsClaim.id == claim.id)) is not None
        await revoke_rights(database, source, now=FOUNDATION_NOW)
        assert (
            await observed_claim_spans(
                database,
                (story.id,),
                definitions,
                workspace_id=WORKSPACE,
                user_id=USER,
                now=FOUNDATION_NOW,
            )
            == ()
        )
