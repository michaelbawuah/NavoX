# SPEC-006 acceptance record

Status: **IN PROGRESS — not accepted and not production enabled**.

This record distinguishes implementation, offline fixtures, live observations and
unmet acceptance gates. Passing foundation tests does not establish the PDF's
claim-extraction, clustering, conversational quality or usability thresholds.

## Evidence observed on 2026-09-29

- Baseline: `main` at `622cb295d2be0e72c10dccae76c305624a0ba988`;
  its six hosted checks succeeded before SPEC-006 work began.
- The first complete local API run had **1 failure / 1,871 passes**. The gate's
  synthetic schema fixture had not been extended for news tables; two aggregate
  metric gates consequently failed. The fixture was updated, with a new negative
  case proving a missing news table blocks publication. Its 16 cases then passed.
- Forty-two news tests passed on SQLite and separately on PostgreSQL 17, covering
  rights, owner isolation, source revisions, exact copies, evidence states,
  original-source withdrawal, background pause/cleanup, conversation ownership,
  idempotency, fresh context reauthorization, citation fabrication and source changes.
- PostgreSQL migration: clean upgrade, downgrade to `0025_ai_drafts_sessions`,
  upgrade again; all **14 news tables** match model metadata.
- A general `alembic check` reported pre-existing schema differences outside news,
  including connector indexes/nullability and `objectives.waiting_since`. These
  have not been silently fixed or counted as a full-schema pass.
- The initial web check passed lint (eight pre-existing CSS specificity warnings),
  typecheck, **172 tests** and production build; these also passed after the final
  conversation UI edits. Shared contracts typecheck passed.
- Final full API preflight passed: **1,879 tests**, Ruff, strict mypy, revision width,
  all required migration tables, deterministic release evaluation, retained SPEC-003/004
  metric gates and whitespace validation.
- Chrome inspected the actual production frontend with clearly labeled synthetic
  API fixtures: three cards, uncertainty labels and Save interaction passed. Both
  1280px desktop and 390px mobile screenshots were inspected; mobile had no
  horizontal overflow. This is not a live-account test, screen-reader audit or
  measured human usability study. See `browser-smoke-20260929.json`.
- One public USGS Atom request ingested and independently read back **7 items /
  7 stories / 0 rejected** in an isolated PostgreSQL database. Original attribution
  and links were preserved; unreviewed claims remained unconfirmed. No article or
  image requests, AI calls, mailbox reads, or owner-database writes occurred.
  Synthetic probe records were removed afterward. See `public-feed-20260929.json`.

## Implemented surface

- Operator-reviewed RSS/Atom source definitions; deny-by-default immutable rights;
  bounded fetches, no redirects, retention, revocation and source controls.
- Identifier-only Temporal source reconciliation with owner/pause checks and
  cleanup even while news serving is disabled.
- Canonical items, conservative exact deduplication, story grouping, claim spans,
  evidence graph, source independence, application-owned verification and changes.
- Authenticated feed/category/story/source/change APIs; explicit private interests,
  saves, dismissals and follows; no inferred sensitive political profile.
- News-specific extraction, synthesis and conversation gateway artifacts. Existing
  provider qualification cannot qualify their new prompt/schema bindings.
- Owned conversation/turn records, rate limits, idempotent dispatch, bounded
  retrieval, source snapshots, post-provider permission checks, and read-time
  correction/revocation checks. Answers retain selections rather than source copies.
- News and story screens, progressive evidence/history disclosure, source links,
  saved stories, interests, source controls, news chat and shared workspace navigation.

## Remaining mandatory work — do not close SPEC-006

1. **Calibrated semantic clustering and extraction evaluation.** The weighted
   scoring contract exists, but uncalibrated similarity never auto-merges. The
   current deployed path performs exact deduplication only. Semantic clustering
   recall/precision and material extraction precision have not been measured.
2. **Automated intelligence pipeline and qualified synthesis.** News prompt/schema
   artifacts and evidence admission are implemented. The follow-up adds a default-off
   background extraction/synthesis coordinator, transactional publication checks and
   a read-only summary endpoint. Trusted automatic evidence adjudication, consumer
   summary integration and news-specific provider qualification/canary evidence
   remain outstanding; authored provider fixtures are not live acceptance.
3. **X integration and acceptance.** The owner confirmed there is no authorized
   X API access. No X credentials, adapter, live feed or acceptance evidence is
   claimed. X trends remains unavailable; social popularity never establishes truth.
4. **Complete news retrieval/deep intelligence.** Current conversations use owned
   permitted items and reject stale context. Query-driven authorized retrieval,
   refresh orchestration, deeper research, timelines, documented coverage comparison,
   related-story retrieval and measured follow-up continuity remain incomplete.
5. **Ranking and full personalization.** Current feeds are recency ordered, with
   explicit category/topic preferences. Separate calibrated importance/trend/relevance
   ranking and material follow notifications remain incomplete.
6. **Today/Subscriptions simplification and measured accessibility/usability.**
   Shared navigation is implemented. The full requested experience refresh and
   actual keyboard, screen-reader, mobile and >=90% unassisted usability evidence
   remain outstanding.
7. **Release acceptance.** Finish A–H demonstrations, independent evaluation
   corpus/thresholds, security evaluation, exact-head hosted checks and review.

No live provider qualification was performed for SPEC-006. The SPEC-005 routing,
shadow, excluded-provider settings, controlled-send evidence and extraction failure
were not changed. No email, calendar or subscription action was executed.


## Follow-up implementation: clustering evaluation and background intelligence

The continuation from `13f3152` preserves the three unfinished local clustering files
and completes their offline regression coverage. See
`../../architecture/spec-006-intelligence-pipeline.md` for the exact boundaries.
A candidate policy or a passing authored fixture is not a production qualification.
The new workflow remains disabled by default and uses the existing task-specific
gateway qualification gate; no new provider evidence or source authorization is created.

The local changes are tested in an isolated checkout without copied `.env` files,
credentials or owner databases. A separate temporary PostgreSQL container is used
for news regressions and migration upgrade/downgrade/upgrade. All safety and
quality gates remain required, including a negative test for a missing new table.
The old persistence-installer branch and its failures are not mixed into this result.
