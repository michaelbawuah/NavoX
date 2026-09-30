# S006-TREND-1 worker report

STATUS: **ready_for_review** (Astra owns acceptance; this file is not an acceptance record)

Task: SPEC-006 trending retrieval continuation, bundle S006-TREND-1.
Workspace: `/Users/mba_steins/NavoX`, branch `spec-006-news-intelligence`,
HEAD `9b36ee44a1b45837bd09b585c4d7afb2bd473c4b`. Four files are modified in the
working tree; nothing was staged, committed, pushed, merged, deployed or enabled.

## Changed paths and behavior

- `services/api/navox/news/retrieval.py`
  - Adds `TREND_FRAMING_PHRASES`, a small documented vocabulary of whole question
    phrases (`what's trending`, `what is hot`, `what are people talking about`,
    `any buzz about`, `right now`, `these days`, ...), plus `strip_trend_framing()`,
    `topical_terms()` and `trending_topic_terms()`.
  - A TRENDING question with no explicit story scope now removes only those framing
    phrases and filters on what remains, instead of searching the evidence for the
    word "trending" or dropping the whole question. Topical words that also look like
    framing survive inside the topical portion: "New York" keeps `new`, "the political
    right" keeps `right`, "viral infections" keeps `viral`, "hot springs" keeps `hot`,
    "Buzz Aldrin" keeps `buzz`, "People magazine" keeps `people`.
  - Framing is removed before the ten-topic bound, so framing words cannot consume the
    cap and hide a real topic. Every other intent still uses `query_terms()` unchanged.
  - Extracts the existing follow-up rule into `followup_priority()` and reuses it, so
    ordering the trending set by observed activity cannot lose previously displayed
    precedence.
- `services/api/navox/news/ranking.py`
  - `order_trending_items()` now groups the already-authorized items by story member
    and orders the groups by the existing `observed_trend_signals(...).sort_key`
    (observed activity only: source additions, recorded changes, fresh reports,
    independent source groups, last update, story id).
  - It never drops an input item. Items whose cluster has no owned activity record
    keep deterministic recency order at the end, and ties use `activity_tiebreak`
    (newest first, then item id). Suppression, ownership, rights and freshness stay
    the caller's query-level responsibility, as before.
- `services/api/tests/test_news_retrieval.py`, `services/api/tests/test_news_ranking.py`
  - New regressions: generic framing asks stay generic, framing-word topics are retained
    (helper expectations) and still select their own evidence while excluding unrelated
    candidates, framing removal precedes the topic cap, non-TRENDING intents keep the
    lexical requirement, activity ordering plus deterministic story/item ties, explicit
    reference and story scope, follow-up precedence, permission/staleness/caps,
    suppressed-story exclusion and the ordering helper's no-drop/tie behavior.

## Verification

Final source state (correction cycle 2, current `retrieval.py` `fce249be`):

| # | Command (from `services/api` unless noted) | Exit | Salient result |
| --- | --- | --- | --- |
| 1 | `pytest tests/test_news_retrieval.py -q` with the superseded correction-1 `retrieval.py` (`f9f9daac`) swapped in | 1 | Red: `9 failed, 36 passed` - every framing-word topic assertion, the cap boundary and the evidence-selection case failed on the reviewed-bad source. Log `validation/red-correction1-trending-topics.txt`. |
| 2 | `pytest tests/test_news_retrieval.py tests/test_news_ranking.py -q` | 0 | 49 passed. |
| 3 | `python -c` sample of 17 real phrasings through `trending_topic_terms` | 0 | Generic asks (`What's trending?`, `What's trending now?`, `What's going on?`, `Any buzz?`, `What's buzzing today?`) return `()`; `New York`, `hot springs`, `viral infections`, `political right`, `Buzz Aldrin`, `People magazine` and `New Zealand` keep their words. Log `validation/framing-vocabulary-samples.txt`. |
| 4 | `pytest` over the 13 news modules (retrieval, ranking, stories, conversations x2, api, following x2, story api, foundation, intelligence, research, jobs) | 0 | 163 passed. Log `validation/focused-news-suite.txt`. |
| 5 | `python -m ruff check .` and `python -m ruff format --check .` | 0 | All checks passed; 372 files already formatted. |
| 6 | `python -m mypy navox` | 0 | Strict mypy clean (236 source files). |
| 7 | `UV_CACHE_DIR=/tmp/navox-uv-cache bash scripts/check-api.sh` (repo root, unchanged script) | 0 | 11/11 gates PASS: Ruff lint, Ruff formatting, strict mypy, Alembic revision width, full API suite **2024 passed** with 3 retained pre-existing deprecation warnings, SPEC-003 and SPEC-004 metric thresholds, Alembic SQL/required schema, deterministic release evaluation, patch whitespace. Log `validation/check-api-2.log`. |

`UV_CACHE_DIR` only redirects uv's cache because `$HOME/.cache/uv` is not writable in
this sandbox; the gate script, thresholds and lock are unchanged. The three warnings
are pre-existing deprecations from `fastapi`/`starlette` test clients and
`navox/api/today.py`.

Superseded evidence for the first cycle (kept, not current: `retrieval.py` `f9f9daac`,
gate log `validation/check-api-1.log` with 2014 passed): the captured dirty baseline had
no topical-term contract (`validation/red-1-focused.txt`), the baseline probe failed
2/29 on topical scoping and follow-up precedence
(`validation/red-baseline-retrieval-probe.txt`), and the baseline ranking unit test
failed because it silently dropped the unindexed item
(`validation/red-baseline-ranking-unit.txt`).

## Source hashes (sha256)

| Path | Captured dirty baseline | Superseded cycle 1 | Final tree (cycle 2) |
| --- | --- | --- | --- |
| `services/api/navox/news/ranking.py` | `a1bab728bfdfd19bfad2631cfd54fac922ab9dbda8d391483e5a688844b1e8bd` | `e88de5bb43eaf35abf80a065e4336b791abac886121a2be866bf08aa97ead23d` | `e88de5bb43eaf35abf80a065e4336b791abac886121a2be866bf08aa97ead23d` |
| `services/api/navox/news/retrieval.py` | `026440552f058217217c0e91644ac0ce1ad33bda6bd1eca44cfa3739a7fa712d` | `f9f9daac8cbf2373fab634d429f92cfc4db09cd221b715d2ffd7bd843f41b588` | `fce249bef1060e1f271e72433b8baec391ea9d30490e71c04db8d163f1b3ced9` |
| `services/api/tests/test_news_retrieval.py` | `bb65e37a8b86e30e51301800ca2cd5951beec7a4952b8d5b1aa56a9ff32104e6` | `af77228fc333d1a0bc4ef2a920792b88a68986822332b0191fdc3f6351862476` | `b3fb91af01d0eb9d6b3fdfa74bb5f2fe5528a4cdb63c66cc1d8621fe3b46c6f4` |
| `services/api/tests/test_news_ranking.py` | (unmodified at baseline) | `7176dac32cf2245fad03770b67c38ef021239171bf03c82a281adbd7b7bd21ea` | `7176dac32cf2245fad03770b67c38ef021239171bf03c82a281adbd7b7bd21ea` |

Baseline bytes are the parent's capture at
`/tmp/navox-spec006-trending-20260929/baseline`. Both source files were restored to the
expected hashes after each substitution probe, verified by re-hash. `ranking.py` is
unchanged by the correction cycle; its cycle-1 behavior and tests were accepted.

## Decisions requiring Astra

1. `TREND_FRAMING_PHRASES` content. Removing a question's own framing is the difference
   between a generic trend ask returning fresh evidence and returning nothing, but the
   vocabulary is a product judgement. It is phrase-based and applied only when
   `intent == TRENDING` and no story scope is set, so framing-word topics keep their
   words; every other intent is untouched. A phrasing outside the vocabulary (for
   example "what's the word on X?") keeps its words and still filters, which is the
   conservative direction this cycle was asked for.
2. `order_trending_items()` no longer re-filters suppressed stories itself. The caller's
   query is the single authorization gate (owner, rights, suppression, expiry,
   freshness), which keeps an ordering helper from silently discarding candidates. A
   retrieval-level regression covers suppressed-story exclusion.
3. Follow-up precedence applies to a trending question only when it carries no topical
   term, mirroring the pre-existing `rank()` rule (`priority ... if not terms else 0`).
4. Unindexed candidates are retained at the end of the ordering instead of being
   dropped. Reachable inputs always have a membership, so this only removes a silent
   loss path.
5. Coverage limit unchanged by design: trending ordering runs over the bounded
   recency-ordered candidate set (`candidate_limit`) and returns at most `item_limit`
   items. It is observed activity over owned permitted evidence, not global popularity,
   importance, truth or X qualification.

## SPEC-006 reconciliation (phase 2)

Sources reconciled: the extracted original specification
(`/tmp/navox-spec006-trending-20260929/spec-006.txt`, PDF SHA-256
`f0c23c289d01440059785494249bd9d4113f2fed746ed69ab6d6813615b3e27c`),
`docs/evidence/spec-006/README.md`, `docs/architecture/spec-006-news.md`,
`spec-006-intelligence-pipeline.md`, `spec-006-followed-updates.md`, and commits
`13f3152`, `61c0801`, `c465919`, `99a8b14`, `b584dde`, `e697c78`, `9b36ee4`.

Implemented and covered by authored tests (not measured acceptance):

- M1 rights/source registry, canonical items, deny-by-default profiles, retention and
  revocation (spec "Source Registry & Rights", "Canonical NewsItem").
- M2 conservative exact deduplication, story membership, source independence, version
  history; the weighted clustering score exists but is not evaluation-calibrated.
- M3 claim spans, evidence graph, application-owned verification states, corrections
  and source-change propagation.
- M4 authenticated feed/category/story/source APIs, story page, summaries, original
  links, saved/dismissed state. Newer: `GET /api/v1/news/trending` observed-activity
  feed (`e697c78`, `navox/api/news_stories.py` `feed("trending")` sorting by
  `observed_trend_signals(...).sort_key`) and explicit entity follows in preferences
  (`9b36ee4`).
- Consumer summary integration is present, not missing: `navox/api/news_stories.py:305`
  returns `summary_view(...)` for a story and `apps/web/src/components/story-intelligence.tsx`
  renders `StorySummaryView` with as-of and sources-changed states (`99a8b14`).
- M6 bounded owned retrieval, intent plans, freshness/expiry, numbered references,
  conversation snapshots and this bundle's TRENDING ordering fix. The TRENDING chat path
  calls `order_trending_items()` directly from `select_evidence()`; it does not consume
  the `GET /api/v1/news/trending` feed endpoint.
- M7 timeline, coverage comparison, paginated what-changed and the followed-story
  update inbox (`99a8b14`, `b584dde`) as read-only views over stored evidence.

Unmeasured acceptance or external blockers:

- Calibrated clustering/extraction quality (>=99% duplicate precision, >=95/90%
  clustering precision/recall, >=95% claim extraction precision) has no independent
  corpus; the deployed path is exact deduplication.
- News-specific provider qualification/canary evidence and trusted automatic evidence
  adjudication remain outstanding.
- X Trends (M5): the owner confirmed no authorized X API access. No adapter, feed or
  acceptance evidence is claimed; `x_trends` stays disabled and `X_TRENDS` intent still
  raises `news_unavailable`.
- Ranking/personalization calibration (spec `trend_score != importance_score !=
  personal_relevance_score`): importance and personal-relevance ranking are not
  implemented; this bundle adds bounded observed-activity ordering only.
- Genuinely incomplete beyond the read-only views: registered automatic refresh
  orchestration for stale-source identifiers, related-story retrieval, wider deep
  research, and all measured quality/usability acceptance.
- The trending API is not surfaced in the web feed: `apps/web/src/components/news-workspace.tsx`
  types `Feed` as `"top" | "for-you" | "saved" | NewsCategory`, so no Trending tab or
  call exists today.
- Usability/accessibility gates (>=90% unassisted task completion, keyboard,
  screen-reader, mobile) have no measured evidence; the earlier browser-fixture write
  was denied and was not retried.
- Demonstrations A-H and exact-head hosted checks on this working tree are not done.
  Hosted run `36594520421` (all six jobs) covers published head `9b36ee4` only and was
  reported by the parent/handoff; it was not independently re-verified here, and PR #21
  is still an open draft.
- `docs/evidence/spec-006/README.md` (172 lines) predates `b584dde`, `e697c78` and
  `9b36ee4`; its "Remaining mandatory work" items 4-7 are partly stale, and the older
  PR #21 prose is staler still. Only Astra owns that record.

## Limitations of this run

No outbound network, live source/provider/account access, owner dotenv read, owner
database, feature activation, migration, web/SDK/CI/lock change or commit was performed.
No local PostgreSQL or Compose execution is claimed; the new tests ran on SQLite plus
the gate's own fixtures. No browser or visual QA was required or attempted for this
backend-only bundle.

## Next dependency-ordered bundle

1. Surface the trending feed in the owned News UI (`Feed` type, tab, bounded fetch,
   labelled recency/coverage caveat) so M4/M8 are not API-only.
2. Independent clustering/material-extraction evaluation corpus and thresholds, which
   is the largest single acceptance gap and blocks the M2/M3 claims.
3. Trusted automatic evidence adjudication plus news-specific provider qualification.
4. Authorized X adapter only if the owner later obtains X access; until then it stays an
   explicit external dependency.

Full logs: `docs/agent-work/spec-006-trending/validation/`.
