# News semantic clustering implementation bundle — Astra architecture

Status: planned, NOT dispatched. Root approves this bounded design; do not enable it
or manufacture calibration/qualification. Existing source configuration remains empty.

## Goal and ownership
Implement a production-callable but default-off semantic clustering pipeline using
real registered embeddings, exact current source rights, durable attempt bounds,
and operator-reviewed calibration. Preserve existing exact deduplication. A model
or numerical score cannot authorize a join. You are not alone in this checkout;
preserve all root Ask/graph/lifecycle/News importance edits. No other worker/delegation,
no paid calls, activation, commit/push, owner DB or external service mutations.

Worker owns new navox/news/semantic_clustering.py, cluster_contracts.py and associated
new tests; additive db/news.py + migration0033; additive settings; minimal stories.py,
news/activities.py, workflows/news.py integration; update migration-head test/schema
list only if needed. Root handles final review, browser and combined validation.

## Authority and architecture
- Retain exact URL/copy identity path unchanged. Semantic joins apply ONLY to an
  unindexed item before first story creation; never merge established stories or
  move items with histories/claims/preferences. Existing clusters are candidates.
- Separate embedding paid work from database locks. News-specific context re-reads
  owner/member/pause, source rights original+current, registry fingerprints, item
  revision/digest/expiry and allowed headline+snippet before and after gateway use.
  Do not use KnowledgeContext pretending a News item is a connected resource.
  No full text or arbitrary URL fetches. Real EMBEDDING profile/artifact/gateway
  from SPEC005, no alternate provider selection, fake vectors or SDK bypass.
- Persist exact namespace (provider/model/registry/artifact/dimension) and item
  revision/digest/policy fingerprint. Vector finite/nonzero/dimension validation
  reuses tested numerical helpers. Read only one exact namespace. Mismatch/missing
  candidate embeddings = incomplete, never silently dropping a plausible candidate.
- Durable per-user UUID request reservations, one attempt per item revision+namespace,
  <=$0.05 max per embedding call; configurable hourly bound default small. No retry
  purchase after failures/timeouts. Bound source batch and workflow duration. Feature
  disabled/unqualified/invalid policy performs no paid call and exact indexing works.
- Candidate set: same owner/workspace/language, explicit policy time/source scope,
  at most100; retrieve101 to detect truncation. Candidate timestamp uses known event
  time separately from publication. All candidates require fresh rights/revision at
  prepare and final join transaction; lock owner and current target story consistently.
- Signal formula stays SPEC006 .40 semantic+.20 entity+.15 temporal+.10 geographic+
  .10 eventtype+.05 topic. Never pretend word overlap is semantic similarity or a
  capitalized string is a resolved entity. Introduce an internal source-cited reviewed
  feature record for canonical entity IDs, geography, event-type/identity/time where
  upstream structured identity is unavailable. Bind exact item revision/policy and
  expiring review. No model/public API writes trusted IDs. Missing material identity
  means UNCERTAIN, independently reported uncertainty stays explicit. This provides
  a safe extraction boundary; automatic feature qualification remains external work.
- Operator deployment policy is separate from CalibrationReport (which remains
  production_qualified=False). It must explicitly bind extractor version, exact
  embedding namespace, catalog/source/language scope, corpus manifest SHA256 and
  CalibrationReport digest, current expiry and reviewer/reference. Validate independent
  reviewer-labelled provenance, holdout targets >=.95precision/.90recall, compatible
  thresholds and exact report/policy binding. Authored/machine corpora cannot qualify.
  No policy is installed by this task. The configured reviewed artifact is trusted
  operator input, never a source/model statement or arbitrary evaluation_reference.
- Use existing select_candidate margin/tie/incomplete behavior. Re-evaluate candidates,
  source rights and policy before the atomic first membership write. Copies still
  share independence origin; a semantic join conveys NO truth/evidence strength.
  Audit chosen namespace/policy/reference/revisions without saving source text.

## Integration behavior
Keep current ingestion's exact indexing when disabled. For an enabled valid pipeline,
allow ingestion to defer only unindexed rows to a dedicated bounded clustering
activity before intelligence; the activity always finishes remaining permitted rows
with exact dedup/new-story fallback, including unavailable gateway/UNCERTAIN. Failures
must not leave freshly ingested items permanently invisible. Workflow history contains
identifiers only, retry uses durable attempts; old histories use workflow.patched.
No network calls in GET/feed projection. No feature/policy/provider registration is
changed. Default feeds preserve current behavior and honest ranking labels.

## Acceptance / evidence
Targeted SQLite and isolated PostgreSQL tests (existing disposable DSN): actual
Registered gateway + synthetic HTTP adapter, rights revoke/pause/member removal
inflight, stale item/catalog/namespace, expired/replaced policy, authored corpus
rejection, source scope mismatch, incomplete101/ties/event incompatibility, crossowner,
concurrent same request/new membership, exact fallback on provider failure, revision
invalidation, original/current rights, no double purchase, no claims/verification
promotion. Temporal sandbox/import verification. Strictmypy/ruff only touched paths;
root runs final full gate. Report exact executed tests and any limitations separately
from independently measured quality. Do not mark original SPEC006 complete.
