# SPEC-006 News semantic clustering — worker implementation report

STATUS: ready_for_review (worker handoff to Astra; this worker does not accept its
own work and does not claim SPEC-006 completion)

## Workspace and scope

- Repository `/Users/mba_steins/NavoX`, branch `spec-006-news-intelligence`
  `@5573ba4345a69cf4d01f793cfa4a6d0dd195a6f6`; pre-existing dirty root work
  (Ask / graph / lifecycle / Search / News importance / web) was preserved. No
  commit, stage, push, deploy, activation, owner-database migration or paid
  provider call.
- Contracts read in full: `CLUSTERING-BRIEF.md`, `NEWS-REMAINDER-DESIGN.md`,
  `AGENTS.md`, the clustering sections of `/tmp/SPEC-006_NavoX_Complete.txt`, and
  root's consolidated acceptance-correction message.

## Correction cycle (root acceptance correction)

This revision answers the four review findings in one coherent pass.

1. **Citations are real permitted spans, not reference strings.**
   `FeatureCitation` is now `{field, span: ClaimSpan}` and the record validator
   requires every citation span to carry the record's own item id and revision.
   `record_feature` requires `Operation.SUMMARY` on original+current rights
   (`permitted_summary_item`), rejects a future review (`invalid_reference`) or an
   expired one (`stale_evidence`), rejects identity drift (`stale_evidence`) and
   validates each span with `quote_at` (`invalid_evidence`).
   `current_feature` re-reads the current permitted item and re-validates every
   span, so a revoked, narrowed or rebuilt item stops applying. A record with no
   identity signal is refused. See
   `navox/news/cluster_contracts.py` (`FeatureCitation`, `ReviewedFeatureRecord`)
   and `navox/news/semantic_clustering.py` (`current_feature`, `_citation_holds`,
   `record_feature`).
2. **Proposals bind the full policy identity, and namespaces are verified.**
   `policy_digest()` gives the immutable identity of one exact policy;
   `ClusterProposal` carries both `policy_reference` and `policy_digest`.
   `commit` now requires a current policy whose reference *and* digest match, and
   whose namespace digest equals the proposal's, and it re-reads the query item's
   stored row through `policy.namespace.digest` verifying the persisted provider,
   model, registry revision, artifact, pipeline version, namespace digest and
   dimension (`_stored_namespace_matches`). Candidate vectors are checked the same
   way in `_evaluate`, and a candidate whose dimension differs from the query
   vector makes the set incomplete instead of scoring a partial comparison.
3. **Deferral is explicit in the workflow payload and recovery is guaranteed.**
   `NewsSourceWork.defer` (default `False`) is set only by
   `news_sources_activity` when a reviewed policy is current, and it travels in
   workflow history. `ingest_news_source_activity` defers only when
   `should_defer(payload, settings, definitions, now)` is true and reports
   `NewsWorkResult.deferred`. The workflow runs `news_clustering_activity` only
   for a deferred payload and, when that activity fails, times out or reports
   unfinished rows, runs the separate, non-paid `news_exact_index_activity`
   (new patch marker `news-exact-recovery-v1`). Cancellation is re-raised at every
   site. A payload or result recorded before these fields existed deserializes
   with `defer=False`/`deferred=False`, so an older history never defers. The
   gateway now converts known unavailability (`AIProviderNotConfigured`,
   `GatewayUnavailable`, `ContextDenied`, registry/validation `ValueError`) into
   "no qualified model" instead of raising, and the clustering activity still has
   a final broad guard that ends in its exact pass.
4. **Lock order and time fencing.** `news_clustering_activity` reads the source
   with `lock=False` and commits that read transaction before any paid work, so no
   News row lock is held across a provider call and the canonical order
   `users -> news_items -> news_sources` is preserved by `reserve_attempt` and
   `commit`. `propose`, `commit` and `vectorize_source` authorize at
   `max(caller_now, real_now)`, so a stale pre-provider instant can no longer keep
   an expired policy or review alive at commit time. `index_item` also refuses to
   let a paid-path exception block exact indexing.

## Changed paths and behaviour

- `navox/news/cluster_contracts.py` (new): span-cited `ReviewedFeatureRecord`,
  `EmbeddingNamespace{,Ref}`, `DeploymentPolicy`, `policy_digest`,
  `validate_deployment_policy`, `active_policy`. Authored/machine corpora, digest
  mismatches, threshold mismatches, weak holdout (<.95 precision / <.90 recall),
  expiry and out-of-scope sources are rejected; `production_qualified` stays
  `False` and no policy is installed.
- `navox/news/semantic_clustering.py` (new): durable per-item-revision+namespace
  reservations, `$0.05` ceiling, configurable hourly quota, News-owned
  `AuthorizedContext` over the SPEC-005/007 `EMBEDDING` profile with
  `allow_fallback=False`, `SemanticClusterer.propose`/`.commit`, `vectorize_source`
  backfill, `record_feature`/`current_feature` review boundary.
- `navox/db/news.py` + `migrations/versions/0033_news_semantic_clustering.py`:
  additive `news_cluster_features`, `news_cluster_embeddings`,
  `news_cluster_embedding_requests`, nullable `news_story_items.match_reference` /
  `match_namespace`.
- `navox/news/jobs.py`: `NewsSourceWork.defer`, `NewsWorkResult.deferred`
  (both default `False`, so older payloads/results stay valid).
- `navox/news/activities.py`: `should_defer`, `clustering_deferred`,
  `news_clustering_activity` (unlocked source read, committed transaction before
  paid work, broad guard, deferred reporting) and the new non-paid
  `news_exact_index_activity`.
- `navox/news/stories.py`: optional clustering engine on `index_item` /
  `index_source`, savepoint-protected first-story creation, last-resort guard so a
  paid-path failure never blocks exact indexing.
- `navox/workflows/news.py` + `navox/workflows/worker.py`: payload-driven
  clustering step and the `news-exact-recovery-v1` recovery step; recovery
  activity registered.
- `navox/core/settings.py`: default-off `news_semantic_clustering_enabled`,
  `news_semantic_clustering_hourly_quota`, `news_semantic_deployment_policy`,
  `news_semantic_calibration_report`.
- Schema/head lists: `.github/workflows/ci.yml`, `scripts/check-api.sh`,
  `tests/test_api_preflight.py`, `tests/test_knowledge_migration.py`, and the
  activity-order/field expectations in `tests/test_news_intelligence.py` and
  `tests/test_news_conversation_refresh_workflow.py`.
- New tests `tests/test_news_semantic_clustering.py`; evidence in
  `clustering-logs/` (see its `README.md`).

Audit rows record provider/model/registry revision/artifact/pipeline version,
namespace digest, item revision, retained digest, rights fingerprint, policy
reference and cost. No row stores headline, snippet or any source text.

## Verification (exact commands, exit status, salient result)

All commands ran from `services/api` with
`UV_CACHE_DIR=/tmp/navox-uv-cache UV_OFFLINE=1 uv run --no-sync ...`.

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `python -m ruff check navox` | 0 | All checks passed (274 files) — `clustering-logs/ruff-navox.log` |
| 2 | `python -m ruff check .` | 0 | All checks passed (whole API tree) |
| 3 | `python -m ruff format --check navox` | 0 | 274 files already formatted |
| 4 | `python -m mypy navox` | 0 | Success: no issues found in 274 source files |
| 5 | `python -m pytest tests/test_news_*.py tests/test_knowledge_migration.py tests/test_api_preflight.py -q` | 0 | 368 passed, 3 skipped (SQLite) — `clustering-logs/sqlite-news-cohort.log` |
| 6 | `python -m pytest tests/test_news_semantic_clustering.py -q` | 0 | 40 passed, 3 skipped (SQLite) |
| 7 | same file with the disposable PostgreSQL DSN | 0 | 43 passed, including both concurrency cases and the row-lock ordering case (log not retained; see `clustering-logs/README.md`) |
| 8 | `python -m pytest tests/test_news_conversation_api.py::test_news_workflows_load_in_temporal_sandbox -q` | 0 | 1 passed (Temporal sandbox prepare of the modified workflow) |
| 9 | Alembic on the disposable container: `downgrade 0032_news_importance` → `upgrade head` → schema query | 0 | 0033 reverted and re-applied; three tables and both `match_*` columns verified — `clustering-logs/pg-migration-0033.log` |

Acceptance-scenario map (all in `tests/test_news_semantic_clustering.py` unless
noted):

- registered gateway + synthetic transport double:
  `test_semantic_join_uses_the_registered_gateway_with_bounded_attempts`.
- citations: `test_feature_citations_must_be_real_permitted_spans`,
  `test_a_cited_feature_stops_applying_when_the_span_leaves_the_permit`,
  `test_future_and_expired_reviews_are_rejected`.
- policy identity and vector metadata:
  `test_same_reference_policy_replacement_blocks_the_commit`,
  `test_candidate_vector_namespace_metadata_mismatch_stays_incomplete`,
  `test_query_vector_dimension_mismatch_blocks_the_commit`,
  `test_commit_uses_the_current_time_not_the_pre_provider_instant`,
  `test_deployment_policy_must_bind_the_reviewed_report_and_catalog_scope`.
- rights revoke / pause / revision in flight:
  `test_inflight_pause_discards_the_vector_and_still_indexes`,
  `test_post_call_fence_discards_a_vector_after_authority_changes[source_disabled|rights_revoked|revision_bumped]`,
  `test_stale_namespace_and_incompatible_events_never_join`,
  `test_narrowed_current_rights_invalidate_a_candidate_vector`.
- candidates: `test_incomplete_candidate_coverage_stays_uncertain`,
  `test_cross_owner_items_are_never_candidates`,
  `test_missing_features_buy_nothing_and_never_join`.
- purchase bounds: `test_reservations_are_idempotent_per_revision_and_namespace`,
  `test_hourly_quota_bounds_paid_attempts_across_items`,
  `test_provider_failure_creates_the_story_without_a_retry`.
- deferral and recovery: `test_pre_patch_payloads_and_results_never_defer`,
  `test_workflow_runs_exact_recovery_when_clustering_fails`,
  `test_workflow_skips_recovery_when_clustering_finished`,
  `test_workflow_skips_recovery_when_nothing_was_deferred`,
  `test_workflow_propagates_clustering_cancellation_without_recovery`,
  `test_clustering_activity_reports_deferred_when_exact_indexing_fails`,
  `test_clustering_activity_survives_an_unexpected_paid_failure`,
  `test_exact_recovery_activity_indexes_without_any_paid_call`,
  `test_clustering_activity_ends_with_exact_fallback_when_gateway_is_absent`.
- lock order: `test_clustering_activity_reads_the_source_without_a_lock`,
  `test_postgres_lock_order_is_user_before_source`.
- concurrency: `test_postgres_concurrent_requests_reserve_exactly_one_attempt`,
  `test_postgres_concurrent_first_membership_keeps_one_row`.
- no claims/verification promotion: happy-path assertions on `story_view`
  (`evidence_pending`, status not `VERIFIED`, source count) and zero claim rows.

## Limitations and explicit non-claims

- No paid provider call was made; spend for this bundle is `$0.00`. No live model,
  source, policy or qualification was created and production registry state is
  unchanged.
- Calibration/authoring fixtures are authored synthetics. They exercise the
  binding and rejection rules; they are not an independently labelled lawful
  corpus and measure no clustering precision or recall.
- The final full News cohort on PostgreSQL could not be re-run after the
  correction cycle: the escalation for that exact command was declined by the
  automatic approval reviewer with an account usage-limit error. PostgreSQL
  evidence for the corrected code is the complete
  `tests/test_news_semantic_clustering.py` run (`43 passed`), the earlier full
  News cohort (`337 passed`, pre-correction) and the 0033 migration round trip.
- `bash scripts/check-api.sh` (the full pre-push gate) was deliberately not run;
  root owns final integration. Web/SDK/extension code is untouched, so no web gate
  was applicable.
- `ruff format --check .` reports two root-owned files
  (`migrations/versions/0031_knowledge_intelligence.py`,
  `tests/test_news_span_options.py`) as unformatted; no file this worker owns is
  unformatted.
- Migration 0033 was applied and reverted only on the disposable container. Root
  owns public-schema migration state; the last capture left that public schema at
  `0033_news_semantic_clustering`, and root has said it will revert it after
  freeze.
- Engine difference observed and asserted: on PostgreSQL a revocation cascades the
  durable reservation immediately; under SQLite's read snapshot the same attempt
  can be observed as `DISCARDED`. Both leave no vector and no membership.
- An ingestion history recorded by the *intermediate, never-deployed* version of
  this bundle (which scheduled the clustering activity without the payload flag)
  would now replay a different command sequence; the deployed baseline histories,
  which never recorded that marker, are unaffected.
- No browser/visual QA was performed: this bundle changes no UI, feed or
  projection path.

## Decisions requiring Astra

1. Payload-driven deferral: only `news_sources_activity` sets `defer=True`, and
   only while a reviewed policy is current; recovery is gated on the recorded
   `deferred` flag plus the new `news-exact-recovery-v1` marker.
2. Two nullable audit columns on `news_story_items` (`match_reference`,
   `match_namespace`) inside additive migration 0033.
3. Reusing the existing `knowledge_embedding@v1` artifact and `EMBEDDING` profile
   for News vectors instead of registering a News-specific artifact.
4. Edits inside root-owned test files: migration head
   (`tests/test_knowledge_migration.py`), ingestion activity order
   (`tests/test_news_intelligence.py`) and the refresh payload field set
   (`tests/test_news_conversation_refresh_workflow.py`).

## Next checkpoint

After Astra's review: freeze these paths, re-run the PostgreSQL News cohort and
the combined gate, complete the intended-environment `0031/0032/0033` round trip,
and decide the artifact/deferral questions above. Semantic clustering remains
default-off and uncalibrated until an operator installs a reviewed policy and a
qualified embedding model exists.
