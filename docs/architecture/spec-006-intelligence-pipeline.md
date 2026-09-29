# SPEC-006: bounded background intelligence follow-up

Status: implementation checkpoint, not SPEC-006 acceptance or provider qualification.
Base: `13f3152751e7c9c136bbbee64581f0580ecac2d9` on `spec-006-news-intelligence`.

## Clustering evaluation

The recovered candidate selector evaluates all bounded candidates, abstains on incomplete
retrieval or ambiguous scores, and never joins a tied match by traversal order. The offline
calibrator separates development and holdout event/story identities, selects a policy only
on development examples, and counts missing candidates, wrong targets and abstentions in
its metrics. Reports explicitly retain `production_qualified=false` and
`runtime_activation=false`. The tests use authored, precomputed signals; they do not measure
semantic clustering quality on real news. No semantic merge is activated by this change.

## Background pipeline

A separately default-off `news_intelligence_enabled` flag gates the background activity.
Successful source-ingestion workflows can invoke extraction, existing application-owned
verification, and synthesis through the unchanged task-qualified NEWS_SYNTHESIS gateway.
The existing chat flag remains separate. A workflow patch marker preserves old-history
compatibility. No workers, sources, provider routes or flags are enabled by installation.

Each story run is recorded before a provider request in `news_intelligence_runs`.
The table is owner-bound to the story and unique for its starting story version. A completed,
failed or interrupted run cannot silently purchase the same version again. Four reservations
per owner/workspace per hour are allowed; each run has two phase ceilings of $0.025, with
all normal gateway eligibility, policy, fallback and cost checks still applying. These are
request limits, not a claim that a provider was invoked or charged.

No database lock spans a model call. The publication transaction reacquires source, owner
and story locks and checks the original context, item revisions, complete bounded story
membership, current rights, pause state and story version before writing. More than twelve
items is rejected rather than silently presenting partial story coverage as complete.
Concurrent workers, cancellation and source changes are covered with synthetic providers.
The paid activity uses one attempt; interrupted runs are not blindly replayed.

Extraction admits exact source spans only. It does not invent independent evidence reviews,
primary-record authority, strong evidence or a human approval. Existing reviewed evidence is
re-evaluated by application logic. Unreviewed claims remain unconfirmed and can appear only
in the uncertainty section of a synthesis. Automatic trusted evidence adjudication still
requires its own evaluated implementation.

Synthesis stores headline/claim IDs, revisions, current claim signatures and trace IDs,
not copied articles or generated prose. The authenticated GET story summary endpoint resolves
text and original links from currently permitted records. Changed, suppressed, revoked,
expired or contradictory evidence withholds the old summary. Publisher headlines remain
explicitly attributed rather than becoming verified facts merely through selection.

The background scheduler excludes processed versions before applying its four-story limit.
Recovery marks processing runs older than ten minutes unavailable. Expired selections are
cleared while the content-free reservation remains to prevent blind retries. The activity
has a thirty-minute timeout for at most four sequential, bounded two-phase runs. Existing
source deletion cascades through story-owned records.

## Deliberate limits

The read-only summary endpoint is backend-only; the consumer summary UI is not connected
in this checkpoint. Same-version retry after failure or summary expiry requires a future
explicit, budgeted recovery mechanism; this release fails closed rather than retrying
unknown provider outcomes. Very large or unavailable story contexts are not silently reduced.

No news-specific model qualification, live canary, calibrated text-to-cluster evaluation,
X access, deep research, ranking calibration, or complete cross-product UX acceptance is
claimed. SPEC-005 routing/shadow state and the approved website remain unchanged.
The old `NavoX-spec006-m1` installer worktree is not the baseline and is not merged here.

## Validation

The complete proposed tree must pass `bash scripts/check-api.sh` with the existing lockfile.
The news regression cohort is also exercised on a separately created disposable PostgreSQL
container, never the owner database. The new migration has an upgrade/downgrade/upgrade
check. Full-schema parity outside the news change is not claimed; existing schema differences
remain outside this checkpoint. Exact-head hosted CI is a separate publication gate.
