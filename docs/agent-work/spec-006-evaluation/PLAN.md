# SPEC-006 next bundle: source-grounded clustering and extraction evaluation

Status: draft brief, not dispatched. The trending retrieval patch is accepted but
still uncommitted, so this bundle waits for that publication and for the three owner
decisions below.

Base: `spec-006-news-intelligence` at `9b36ee44a1b45837bd09b585c4d7afb2bd473c4b`
plus the accepted trending retrieval patch.

## Why this bundle is next

Acceptance item 1 (calibrated clustering and extraction evaluation) gates item 2's
trusted evidence adjudication and blocks any calibration claim. The scoring contract,
candidate selector and offline calibrator already exist in
[cluster_evaluation.py](/Users/mba_steins/NavoX/services/api/navox/news/cluster_evaluation.py:1)
and report `production_qualified=false`. What is missing is a corpus with recorded
provenance and a measurement path from real text, plus material-extraction precision
against the same evidence contract.

## Contract Astra fixes before dispatch

- A corpus record carries source identity, retrieval or capture timestamp, rights
  basis, text digest and the label's own provenance. Repository content redistributes
  no text the source does not permit.
- A label is never described as human approval unless a named reviewer approved it.
  Machine or authored labels stay labeled as such.
- Signals are computed from source text through the same code path production uses.
  Precomputed-signal reports must say so and cannot claim measured clustering quality.
- Reports retain `production_qualified=false` and `runtime_activation=false` until a
  separate qualification imports real evidence.
- No live provider call, owner database, credential, X access or model spend.

## Owner decisions required

1. Corpus source: which article text may be captured for evaluation, and under what
   rights basis, given that no live publisher access is authorized in this session.
2. Labeling authority: who decides join/create/extract correctness, and whether that
   reviewer is named in the report.
3. Whether extraction precision is measured on the same corpus or a separate one with
   its own manifest.

## Bundle scope once decided

- `services/api/navox/news/cluster_evaluation.py` and a new extraction-measurement
  module beside it.
- `services/api/tests/test_news_cluster_evaluation.py` plus a new extraction test.
- `services/api/tests/fixtures/spec006/` corpus, manifest and expected metrics.
- `docs/architecture/spec-006-intelligence-pipeline.md` limits update.
- No production activation, flag flip, migration, provider route or gate change.

## Required verification

- Deterministic metrics for the same corpus bytes, with the corpus hash in the report.
- Red probes for split leakage, missing candidates, abstentions counted inside recall,
  wrong-target joins and incompatible-event joins.
- Material-extraction precision and recall with the unmeasured cases left explicit.
- Unchanged `bash scripts/check-api.sh` on the final proposed tree, plus the
  repository's PostgreSQL check for any persistence change.

## Non-goals

Semantic merge activation, calibrated importance or trend ranking, news-specific
provider qualification, refresh orchestration, related-story retrieval, a dedicated
Trending web tab, X access and final A–H acceptance.
