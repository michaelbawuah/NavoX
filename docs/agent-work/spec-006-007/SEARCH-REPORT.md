# SPEC-007 S007-SEARCH report (connected retrieval, search API and /navox search)

STATUS: ready_for_review (not self-accepted; Astra owns acceptance)

## Second correction cycle (SEARCH-FINAL-FENCES.md)

All six remaining fences are implemented with regressions, on top of the first
correction bundle. Migration bytes are unchanged (0029 and 0030 untouched).

1. **Publication applies current exclusions and scope.** `publication_check` now
   takes the freshly loaded exclusions and re-reads each candidate's current
   parent, type, source connection and deletion state from the database instead
   of trusting the earlier copy, then applies every exclusion kind before
   re-authorizing and re-fencing. Regression:
   `test_publication_reapplies_exclusions_against_fresh_source_scope` moves a
   source's parent without a body-hash change, adds a FOLDER exclusion for the
   new parent, and asserts nothing survives; a TYPE exclusion case follows.
2. **Rechecked native evidence replaces the first read.** Native resources and
   facts now come from the publication re-read (`allowed_native`,
   `rechecked_native.facts`), never from the earlier copies, and the adapters
   force fresh reads (`populate_existing` on commitments and News story ids,
   `await database.refresh(row)` for subscription rows returned by the owned
   service). Regressions:
   `test_native_recheck_values_replace_stale_selections` (monkeypatched two-phase
   same-key change asserts the new title/version/fact and no old fact) and
   `test_native_evidence_reflects_current_values_across_requests`.
3. **News cites the story's anchor item.** The adapter selects the source whose
   id is `story.anchor_item_id` rather than `sources[0]`, and provenance now
   records an honest `content_basis` (stored snippet vs metadata) without
   implying summary permission. Regression:
   `test_news_citation_uses_the_story_anchor_source` attaches a newer source
   with a different headline and URL and asserts the anchor's headline and URL
   are cited.
4. **Indexing trusts nothing from the caller.** The connection is reloaded by id
   and its workspace, owner and definition must match; the owner must exist and
   be unpaused; membership and the legacy anchor are re-checked; and `force=True`
   can no longer rebuild a retired projection. Regressions:
   `test_stale_caller_connection_and_paused_owner_cannot_index` (revoked
   capability in the database while the caller's copy still claims it) and
   `test_force_never_rebuilds_a_retired_projection`.
5. **Revision fencing covers every index state.** `_revision_current` now
   requires a non-null stored hash that matches the live canonical hash,
   including for `NO_CONTENT`, and `load_resource_detail` re-reads the index row
   with `populate_existing` at publication and requires the same state and hash
   it used to load chunks. Regressions:
   `test_resource_detail_denies_when_a_no_content_revision_moves` plus the
   existing drift case.
6. **No truncation oracle.** `eligible_identities` now restricts the bounded
   scan to rows carrying a live VIEW grant naming the caller, the workspace or
   the public principal (`can_view_resource` remains the final authority), and
   text matching, counts and truncation are computed only over those identities.
   Regression:
   `test_candidate_truncation_is_not_a_private_existence_oracle` compares member
   coverage before and after seeding 210 private matching rows (identical
   `examined`/`returned`/`truncated`) while the owner still gets an honest
   `truncated=True`.

Two further corrections requested during this cycle:

- **News flag respected.** `_news_evidence` returns nothing unless
  `settings.news_feed_enabled` is true, so search cannot bypass the operator's
  News gate; `news_settings()` in the fixture enables it explicitly and the
  flag-off case has its own assertion.
- **Mobile 200% layout.** `search-workspace.module.css` gained
  `min-inline-size: 0` for the mode fieldset, `min-width: 0`/`max-width: 100%`
  for fields, inputs, selects, panels, lists and the grid, and wrapped mode
  buttons, addressing root's 320px/200% overflow report.
- **PostgreSQL test isolation.** `tests/knowledge_search_support.py` now returns
  a `TestDatabase` that creates a unique `knowledge_search_<hex>` schema with
  `search_path` per engine and drops it (with both engines) on `dispose()`,
  matching the repository's isolated-schema fixtures. All four fixtures use it.
  The PostgreSQL path could not be pre-validated from this sandbox (outbound
  socket to the disposable container is blocked with `PermissionError`), so
  root's PG run remains the evidence.

Second-cycle verification: knowledge modules + preflight `202 passed`; API gate
`2305 passed, 4 warnings in 206.90s`, all 11 gates PASS
(`/private/tmp/navox-fence-gate.log`); Ruff and strict mypy clean; web lint 0
errors, 215 tests, build prerenders `/navox/search`.

Task: S007-SEARCH plus the consolidated correction bundle in
`docs/agent-work/spec-006-007/SEARCH-REVIEW.md`. Workspace
`/Users/mba_steins/NavoX`, branch `spec-006-news-intelligence`, baseline HEAD
`5573ba4345a69cf4d01f793cfa4a6d0dd195a6f6`. No commit, push, merge, deploy,
publication, provider call, dependency or credential change.

Tree fingerprint at the end of the final gates (tracked + untracked,
non-ignored files): `af1abe7b995e22460381a887194220923283f65056ba290ab25e5caf457d1ae3`.
Full API gate ran 2026-09-29 21:38:04Z-21:41:36Z with a before/after fingerprint
of `480cc379...` / `af1abe7b...`; the only files written during that window were
root's documents (`NEWS-REMAINDER-DESIGN.md`, `CHECKPOINT.md`), so no source or
test file changed under the gate.

## Correction bundle: all ten points

1. **Content never loads before authority.** `_candidate_rows` now selects
   explicit identity/authority columns instead of whole `KnowledgeResource`
   entities, `permissions.can_view_resource` uses `load_only(...)` over an
   authority column set, `_canonical_source_is_live` selects only
   `ConnectorResource.deleted`, and the connection lookup is limited to
   authority columns. Regression:
   `tests/test_knowledge_privacy.py::test_denied_retrieval_never_selects_stored_content`
   captures every statement and asserts no projection list mentions
   `normalized_text`, `metadata` or `canonical`, plus a second case asserting a
   foreign-workspace search never selects `normalized_text` at all.
2. **No existence oracle through counts.** `_no_content_matches` is deleted.
   `Coverage` replaces the `unavailable` map with `not_searchable` plus
   non-sensitive `partial_reasons`, `examined` counts only authorized
   candidates, and the not-searchable count is a union of authorized candidate
   keys so overlapping retrievers cannot double count or probe private rows.
   Regression: `test_no_content_counts_are_authorized_only` seeds a private
   typed record with no retained content and asserts a second member sees
   `not_searchable == 0` and no reason code while the owner sees `1` and the
   `SOURCE_CONTENT_NOT_RETAINED` reason.
3. **Resource detail is fully fenced.** `load_resource_detail` re-checks the
   account, exclusions (SOURCE/FOLDER/RESOURCE/TYPE), authority and the source
   revision before reading chunks and again before returning; `NO_CONTENT`
   returns no title, URL or chunks. Regressions:
   `test_resource_detail_honours_exclusions_and_revision_drift` (all three
   exclusion kinds and revision drift) and
   `test_resource_detail_reports_missing_content_without_a_body`.
4. **News evidence uses the owned services.** `_news_evidence` now selects only
   `NewsStory.id`, then calls `owned_story` and `story_view` with
   `catalog(settings)`, so ownership, suppression, the original/current rights
   intersection, retention windows and URL validation all come from News.
   `verification_status` and `lifecycle_status` are separate provenance fields.
   The previous triple-unpacking crash is gone: regressions cover a non-empty
   seeded story end-to-end (service and API), a revoked-rights source, and a
   source that never allowed snippet storage (headline only, no excerpt).
5. **Native filters and revalidation.** `domain_requested` applies
   `plan.types`, per-domain exclusions, and treats a connected-source filter as
   native-excluding with an explicit
   `CONNECTED_SOURCE_FILTER_EXCLUDES_NATIVE_DOMAINS` reason; native evidence is
   collected again with freshly loaded exclusions before publication, and the
   account is re-checked there. Regression:
   `test_native_domains_respect_type_and_connected_source_filters`.
   `RESOURCE` exclusions now validate an owned, currently visible connected
   resource and return 422 for anything else, so native ids can never reach the
   write (see point 8).
6. **Typed facts and topical constraints.** Connected structured rows emit
   `StructuredFact`s with labels and authority from shipped mappings
   (`Event start` / `Calendar (source system)`, `Due` / `Canvas (source
   system)`), `structured_retriever` requires a topic match unless the plan
   intent is a date/state window, and native domains are now collected for every
   query rather than only when STRUCTURED is selected. Regressions:
   `test_connected_structured_resources_emit_typed_facts` and
   `test_unrelated_keyword_queries_do_not_return_typed_rows`.
7. **Projection cannot weaken or linger.** Sensitivity is clamped to at least
   `PERSONAL` (no operator configuration surface grants lower), removed source
   fields are cleared instead of carried over, a deleted canonical source
   tombstones the projection (text/title/URL cleared, chunks deleted) and a
   retired projection is never rebuilt, and indexing reloads the stored
   canonical row and requires current owner membership plus an active legacy
   anchor. Regressions in `tests/test_knowledge_indexing.py`:
   sensitivity clamp, removed-field clearing, retirement and non-revival, and
   stored-row/membership authority.
8. **Exclusion writes are validated, never 500.** `ExclusionCreate` rejects
   mixed or incomplete targets in Pydantic; `create_exclusion` verifies an owned
   connection or a visible connected resource before any write, maps
   `IntegrityError` to the same generic error, and the API returns 422 without
   echoing database detail. Regression:
   `test_exclusion_writes_reject_mixed_or_foreign_targets_without_500`, which
   also asserts a valid write still succeeds afterwards.
9. **Consumer UI races and wording.** A `RequestGate` makes only the newest
   request able to publish response, busy and error state; hiding a source
   invalidates in-flight work and clears the displayed response
   synchronously; a failed search clears the previous results; sidebar
   refreshes are gated too. The date filter now sends start-of-day and an
   exclusive next-midnight end, follow-up suggestions render as plain guidance,
   and the raw intent line and "Typed details" heading are gone (replaced by a
   plain result count and "Key dates"). Regressions: `RequestGate` supersession
   and invalidation tests plus `exclusiveEndOfDay`/`startOfDay` boundary tests.
10. **Snapshot identifier.** `permission_snapshot_id` is now an opaque
    `ps-<sha256 prefix>` derived from the trace, scope and a nonce, so it is
    unique, collision-resistant and never a truncation of the trace. Regression:
    `test_evidence_bundle_snapshot_is_unique_and_never_truncates_the_trace`.

Accepted scope limits were preserved: bounded ILIKE lexical search, default-off
feature, no ASK/graph, no runtime indexing hook, and no edits to the 0029
migration (its bytes and tests are unchanged).

## Verification

Environment: `UV_CACHE_DIR=/private/tmp/navox-uv-cache`, `UV_OFFLINE=1`,
locked `services/api/.venv`, `NAVOX_ISOLATED_BUILD=1` for web commands.

| Command | Exit | Result |
| --- | --- | --- |
| `pytest tests/test_knowledge_*.py -q` (9 modules) | 0 | 177 passed |
| `pytest tests/test_api_preflight.py -q` | 0 | 17 passed |
| `ruff format .` / `ruff check .` | 0 / 0 | only the Ruff formatter touched files; all checks passed |
| `mypy navox` | 0 | no issues in 253 source files |
| `bash scripts/check-api.sh` | 0 | 11/11 gates, `2297 passed, 4 warnings in 203.18s` |
| `npm run lint --workspaces --if-present` | 0 | 0 errors (9 pre-existing warnings) |
| `npm run typecheck --workspaces --if-present` | 0 | web, contracts, connector-sdk, ui |
| `npm run test --workspaces --if-present` | 0 | 215 web tests (29 files) |
| `npm run build --workspace=@navox/web` | 0 | `/navox/search` prerendered |

Gate detail (`/private/tmp/navox-fix-gate.log`): Ruff lint, Ruff formatting,
strict mypy, Alembic revision width, full API suite, SPEC-003 thresholds,
SPEC-004 thresholds, Alembic SQL and required schema, deterministic release
evaluation (`invariants_passed: 11/11`, `control_coverage.passed: 11/11`,
`malicious_rejection_rate: 1.0`) and patch whitespace all PASS.

## Retained limitations

- PostgreSQL execution of 0030 remains root-owned; my evidence is ORM/metadata
  equivalence, offline PostgreSQL DDL render of the whole chain and SQLite
  execution of every new constraint and cascade.
- Browser QA of `/navox/search` is root-owned. This session has no
  `mcp__node_repl__js` tool and the sandbox refuses `listen`, so no dev server,
  screenshot or click-through was produced here; layout and states are covered
  by static-render tests plus the prerendered build.
- The client race and exclusion-refresh regressions are expressed as
  `RequestGate` and date-helper unit tests. This repository has no DOM test
  harness (no jsdom/testing-library) and adding a dependency is out of scope, so
  the component's wiring is verified by types, static render and those guards
  rather than by a simulated DOM.
- Retrieval bounds are unchanged: 200 candidates, 100 fused results, 600 chunk
  rows per request, `ILIKE` matching with Python-side scoring and no stemming or
  semantic fallback. `coverage.truncated` reports the bound honestly.
- Exclusions remain user-scoped preferences; there is no workspace-level
  exclusion scope, authored purge history, or async cleanup worker yet.
- `apps/web/next-env.d.ts` is rewritten by `NAVOX_ISOLATED_BUILD=1`; it was
  restored to the committed content after each build, so any further isolated
  build will re-apply that churn.

## Decisions requiring Astra

1. Semantic and graph modes stay explicitly unavailable; root's new embedding
   transport is deliberately unconsumed in this phase.
2. `knowledge_enabled` defaults off; enabling it in tests is not deployment.
3. Indexing stays callable-only (`backfill_workspace`,
   `index_connector_resource`) with a 200-resource batch and no runtime hook.
4. `KnowledgeResourceIndex` remains a separate 1:1 table so the accepted 0029
   migration stays byte-identical.
5. Sensitivity has no configuration surface that permits below `PERSONAL`; if a
   future operator policy should allow lower, it needs an explicit reviewed
   configuration field.

## Next checkpoint

Correction bundle complete and gated on the frozen combined tree. Outstanding:
root's combined PostgreSQL suite, hosted CI on a published head, and root's
browser rerun of the corrected build. Later phases: semantic retrieval through
the SPEC-005 embedding gateway, bounded graph, sessions with revalidation, ASK
with a qualified provider, life-cycle cleanup, and workspace-scoped exclusions.
