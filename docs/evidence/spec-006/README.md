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
   current deployed path performs exact deduplication only. Offline measurement
   machinery with a corpus provenance contract now exists
   (`navox/news/cluster_evaluation.py`, `navox/news/extraction_evaluation.py`), but
   no corpus with a recorded rights basis exists, so semantic clustering
   recall/precision and material extraction precision remain unmeasured.
2. **Automated intelligence pipeline and qualified synthesis.** News prompt/schema
   artifacts, evidence admission, a default-off background extraction/synthesis
   coordinator, transactional publication checks, a read-only summary endpoint and
   consumer summary integration are implemented. Trusted automatic evidence
   adjudication and news-specific provider qualification/canary evidence remain
   outstanding; authored provider fixtures are not live acceptance.
3. **Popular rumor acceptance.** The owner removed X integration from SPEC-006
   scope on 2026-09-30. A rights-cleared, independent rumor corpus and live source
   demonstration must still show that repetition or popularity cannot establish
   truth or promote an uncorroborated report into an established Top story.
4. **Complete news retrieval/deep intelligence.** Bounded permission-before-query
   lexical retrieval, freshness checks, numbered follow-ups, timelines, documented
   source comparison, paginated changes and bounded related-story reads are
   implemented. Broader research and measured follow-up continuity remain
   incomplete. These read-only views do not establish unrestricted or semantic
   research.
5. **Ranking and full personalization.** Explicit category/topic/entity preferences,
   saves/follows, a material followed-story update inbox, an observed-activity
   trending API and a Trending tab in the web feed are implemented. Trending
   conversation retrieval is locally reviewed below. Separate calibrated
   importance/trend/relevance ranking remains incomplete; observed activity is
   neither importance nor truth, and the update inbox does not establish external
   notifications.
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

## Unattended retrieval continuation — 29 September 2026

The next batch adds bounded, owned query retrieval, explicit prior-answer numbering,
per-turn option persistence and a single-attempt generation reservation. Source
policies are checked before text matching; publication and cached answers recheck
current source revisions and story availability. Timeline, source comparison and
paginated change endpoints read existing evidence without generating new prose.

This is lexical retrieval over connected records, not unrestricted web research or
measured semantic retrieval. Stale-source refresh identifiers are prepared, but the
automatic refresh scheduling integration is not registered in this batch. Related
story search, calibrated trend/importance ranking and the broader product refresh
remain open. Existing live acceptance gaps above are not waived.

The 26 new retrieval/research regression cases pass in an offline process that
cannot open the owner's environment files or make outbound connections. The first
full preflight had 1,948 passing tests but failed formatting and whitespace due to
an extra trailing blank line in the workflow file; it is retained as a failed run.
That formatting issue was corrected, and source permission-before-search cases
were added. The final full gate must be rerun for the final source tree.

Final source preflight: **1952 tests passed**, with three retained warnings; all locked
API gates passed. The final source hashes matched the pre-run snapshot. A further
expiry regression is included in that total. See `retrieval-validation-20260929.json`.
The new PostgreSQL cases are now part of hosted CI; no local PostgreSQL execution
is claimed for this retrieval batch. GitHub Actions run `36538892681` completed
successfully on exact head `c465919836845e556bae31d2b6e20ab92f052a97` across
API quality, Web quality, Chrome extension quality, dependency security,
evaluation/hardening and Compose integration.

## Morning UI continuation — 29 September 2026

The News interface now renders source-backed summaries and attributed headline labels,
numbered facts for the latest eligible answer, and on-demand Timeline, Compare sources
and paginated What changed views. Story/version keys reset cached detail state;
non-ready answers do not render retained facts. Busy conversations cannot issue a
numbered follow-up against an older displayed turn. No new external actions are added.

Today, News and Subscriptions share the labeled Ask control with associated help text.
The read-only subscription query UI uses existing registry endpoints, honors bounded
renewal windows, distinguishes unknown prices and currencies, indicates truncated
match lists, aborts obsolete requests and resets when the registry refreshes.
Existing cancellation and email approval/execution paths are unchanged.

Morning local gates passed: full locked API preflight (1,952 tests, three retained
deprecation warnings); all workspace lint/typecheck/tests (180 Web tests in 24 files
and eight extension tests); production build. Nine CSS specificity warnings remain
(eight pre-existing plus one in the new Ask styling). No checks were weakened.
Initial formatting and effect-dependency errors are retained in the local logs.

The validation build uses the fixed opt-in NAVOX_ISOLATED_BUILD=1 output directory
.next/spec006-check, without replacing the running preview's .next build. Next's
generated declaration imports were restored to their pre-run values afterward;
final declaration lint/typecheck and whitespace are checked again before publication.
Owner dotenv reads and outbound networking were denied in the test process.

The attempted standalone synthetic browser-fixture write was blocked and not retried
or rerouted. New browser interactions, screen-reader checks and measured human task
completion are therefore NOT claimed. UI implementation and static regressions do
not close the original usability gate. News-specific model qualification, independent
clustering/extraction quality, trusted evidence adjudication, authorized X, broader
fresh-retrieval orchestration, ranking and final A–H acceptance remain outstanding.
This remains an incomplete SPEC-006 draft; it does not authorize SPEC-007 sign-off.

## Trending retrieval continuation — 29 September 2026

The original eleven-page specification was read again from the owner's PDF and its
SHA-256 verified as
`f0c23c289d01440059785494249bd9d4113f2fed746ed69ab6d6813615b3e27c`.
The mandatory-work list above now reflects newer implementation. Earlier sections
retain their historical test counts, failures and limitations.

| Work | Implemented | Tested / evidence | Publication | Accepted |
| --- | --- | --- | --- | --- |
| Followed-story material updates and bounded research views (`b584dde`) | Yes | Authored tests; baseline hosted checks | Committed and pushed in draft PR #21 | Full-spec acceptance open |
| Observed-activity trending feed (`e697c78`) | API | Authored tests; baseline hosted checks | Committed and pushed in draft PR #21 | Calibration and end-to-end acceptance open |
| Explicit entity follows (`9b36ee4`) | Yes | Authored tests; baseline hosted checks | Committed and pushed in draft PR #21 | Measured personalization acceptance open |
| Trending retrieval patch (published 29 September 2026) | Yes | 49 retrieval/ranking tests, 163 News tests, full 2,024-test API gate | Committed and pushed on `spec-006-news-intelligence`; PR #21 stays draft | Astra local patch review accepted; hosted checkpoint and SPEC-006 acceptance open |

Global TRENDING retrieval orders the bounded fresh, permitted candidate pool by
existing observed story activity. A documented phrase vocabulary removes trend
question framing before the ten-term topic limit. Topic words such as New York,
political right, viral infections, hot springs, Buzz Aldrin and People magazine
remain searchable. Explicit story/number references, prior-answer precedence,
ownership, rights, suppression, freshness and limits retain regression coverage.
The chat path calls the ranking helper directly; it does not call the separate
trending feed endpoint. This remains lexical retrieval with bounded recency
candidates, not semantic intent understanding or global trend measurement.

Astra reviewed the captured three-file dirty baseline and the final four-file patch
for contract compliance and permission boundaries. One correction replaced an
overbroad word filter that removed topic words and applied the term cap too early.
The worker retained failing regression probes. The first 2,014-test passing gate is
superseded by the final **2,024 tests passed / three existing deprecation warnings**.
The unchanged `bash scripts/check-api.sh` passed all eleven gates using the current
lock; Ruff formatting and strict mypy also passed. Final source hashes match the
worker's evidence. No web, SDK, schema, CI, dependency or feature-flag changes were
made. No local PostgreSQL/Compose or new browser acceptance is claimed.

Astra independently rechecked PR #21: open, draft and unmerged at
`9b36ee44a1b45837bd09b585c4d7afb2bd473c4b`. Run `36594520421` succeeded in
API, Web, Chrome extension, dependency security, evaluation/hardening and Compose
integration on that published head. Those results do **not** cover the trending
retrieval patch, which was prepared afterwards on the same branch. That patch is
accepted locally and published in its own commit on `spec-006-news-intelligence`; a
fresh hosted checkpoint on its exact head is still required, and no merge or
production activation has occurred. The separate SPEC-007 draft's 601-file content
manifest remains unchanged.

Native worker session `01a0ee3c-ea9d-78e0-81f8-d98ceaa7e01c` records
`deepseek/deepseek-v4.1-flash`; router metadata records successful `deepseek`
provider requests. Astra remained the planner/reviewer. No paid setup probe ran.

The resumable checkpoint and worker evidence remain local working records under
`docs/agent-work/spec-006-trending/` (plan, checkpoint, worker report and validation
logs), which this repository does not track.
Independent quality evaluation, trusted evidence adjudication, news-specific
provider qualification, refresh/research completion, calibrated ranking and measured
accessibility/usability/A–H demonstrations remain open. Authorized X access remains
an external dependency. **SPEC-006 is not accepted.**

## Offline acceptance window — 29 September 2026, afternoon

Three bounded bundles were implemented, tested and accepted locally on
`spec-006-news-intelligence` after the trending retrieval patch. The coordinator
committed, pushed, merged and enabled nothing: the working tree is the proposed
commit, and no hosted run covers it yet.

| Bundle | Implemented | Tested / evidence | Publication | Accepted |
| --- | --- | --- | --- | --- |
| S006-SURF-1 news discovery surfaces | Trending feed tab wired to `GET /api/v1/news/stories/trending` with observed-activity labeling; `GET /api/v1/news/stories/{id}/related` bounded to five shared claim digests; story-screen Related stories section | 10 API tests, 197 web tests in 27 files, web lint/typecheck/build, unchanged gate 2030 tests | Uncommitted | Astra local acceptance |
| S006-EVAL-1 evaluation machinery | Corpus provenance contract carried into the calibration report; extraction measurement module with precision, recall, exact-span rate, unmatched labels, spans outside labels, abstentions, explicit unmeasured state, bounded read-only adapter over admitted claims, non-overwriting CLI | 47 focused tests, strict mypy clean, CLI measured/refusal/unmeasured runs, unchanged gate 2054 tests | Uncommitted | Astra local acceptance |
| S006-ADV-1 adversarial regressions | Specification test list as explicit regressions: circular sourcing, syndication, rumor, retraction, stale video, mismatched headlines, prompt injection, zero-tolerance invariants | 13 new tests, 229 news tests, unchanged gate 2067 tests | Uncommitted | Astra local acceptance |
| S006-REFRESH-1 refresh orchestration | `prepare_news_conversation_activity` is now registered: `NewsConversationRefreshWorkflow` refreshes the turn's stale sources through bounded `NewsSourceIngestionWorkflow` children before the answer, behind patch marker `news-conversation-refresh-v1`, with refresh failures never blocking the answer | 7 new workflow tests, 125 focused news workflow/activity tests, unchanged gate 2074 tests | Uncommitted | Astra local acceptance |

The adversarial bundle found and fixed a real defect: `review_evidence` accepted an
item that was not a current member of the claim's story, so an unrelated owned item
could make a claim `CORROBORATED` with `independent_supports=2`. The fix in
[evidence.py](../../../services/api/navox/news/evidence.py:204) requires current
story membership and a matching revision, mirroring `admit_claim`. The fix sits at
the adjudication write boundary; a read-time membership filter in `evaluate_claim`
was considered and deliberately not added, because membership can legitimately
change when a story is re-clustered while the recorded quote stays valid.

Status against the specification's measurement gates, which this record carries from
[the architecture note](../../architecture/spec-006-news.md:138):

| Required measurement | Threshold | Status |
| --- | --- | --- |
| Exact deduplication precision | >=99% | Implemented and regression-tested; no corpus measurement |
| Story clustering precision / recall | >=95% / >=90% | Harness with provenance ready; unmeasured |
| Material claim extraction precision | >=95% | Harness ready; unmeasured |
| Source attribution accuracy | >=99% | Attribution implemented and tested with fixtures; unmeasured |
| Independent-source counting | >=98% | Independence groups implemented with adversarial coverage; unmeasured |
| Grounded chat claims / freshness compliance | >=98% / >=98% | Permission-first retrieval, freshness and citation checks tested; unmeasured |
| Follow-up continuity | >=95% | Numbered follow-ups and prior-answer precedence tested; unmeasured |
| Correction / retraction propagation | 100% / 100% | Covered by regressions and read-time withholding; not measured across a corpus |
| Fabricated URLs or unsupported definitive claims | 0 | Explicit regressions now exist; two defects were found and fixed during SPEC-006 |
| Rights violations, cross-user leaks, unauthorized actions | 0 | Covered by tests; this is not a security certification |
| Unassisted completion of core News tasks | >=90% | No human study has been performed |

The corpus-based rows require independently labelled source material with a recorded
rights basis. Human usability requires participants and observed task completion, not
an article corpus. News-specific provider qualification and canary evidence require
live provider access and spend authorization. Authorized X access remains unavailable
in the recorded evidence, so no adapter or feed is claimed. These are separate
acceptance dependencies; they do not remove the implementation gaps listed above.

**SPEC-006 is not accepted.** Discovery surfaces, related-story reads, refresh
orchestration and evaluation machinery have implementations and regression coverage.
They do not complete calibrated semantic clustering, trusted automatic evidence
adjudication, broader research, or separate importance/trend/relevance ranking.
Refresh has an offline workflow harness; true pre-patch Temporal replay remains
unverified. Corpus, live-provider, X, and human usability gates remain distinct.
Hosted CI run `36610643121` passed all six jobs on published head
`5573ba4345a69cf4d01f793cfa4a6d0dd195a6f6`; it does not cover this uncommitted tree.

## News provider diagnostics and question routing — 29 September 2026

The continuation verified all six baseline hosted jobs at `5573ba4`, preserved the
existing dirty tree, and found a consumer-path defect: global News chat always sent
`CURRENT_NEWS`, so natural trending questions did not reach activity retrieval.
The UI now leaves global intent to a narrow server-owned request-phrase classifier;
explicit story intent and all scope/freshness/reference bounds remain intact. An
X-specific trend question stays unavailable instead of being answered as publisher
activity. Sixty-one focused question/retrieval/API tests passed.

The user authorized existing approved providers and a **$10 total session cap**.
Candidate diagnostics used only ten fixed, newly authored public fixtures, the
existing enabled/priced models from registry revision 6, and isolated in-memory
public-evaluation grants. The serving registry, rollout, account source policies,
owner data, and feature flags were not changed. These diagnostics cannot be imported
as production qualification and do not measure independent real-news quality.

| Candidate | OpenAI gpt-5.6-luna | Gemini gemini-3.5-flash-lite | Claude haiku-4-5-20251001 |
| --- | --- | --- | --- |
| Original News v1 | Request rejected; remaining cases not attempted | 8/10 | 4/10 |
| Strict OpenAI wire-schema fix | 7/10 | Not rerun | Not rerun |
| News v2 with exact server-counted headline span options | 10/10 | 8/10 | 8/10 |

The first rejection exposed an OpenAI strict-schema compatibility defect: the News
span schema had an optional `claim_type` default, but strict output requires every
property to be required. The wire adapter now narrows optional properties to required
and strips default annotations without changing the locally validated canonical
schema. See [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

The initial successful responses also exposed incorrect character offsets. Versioned
v2 News prompts can copy exact application-counted headline spans; v1 prompt/schema
bytes remain unchanged and have digest regressions. No span is repaired after model
output, and a supplied span is neither proof nor an instruction channel. Existing
validators still reject truncated qualifiers, fabricated citations, empty synthesis
sections, and disputed claims placed in definitive sections. The retained v2 Gemini
and Claude failures mean those candidates are not accepted for News deployment.

Evidence: `provider-diagnostic-20260929.json`,
`provider-diagnostic-openai-wire-20260929.json`, and
`provider-diagnostic-span-v2-20260929.json`, with non-overwriting reservation records.
Known estimated usage cost across these runs: **$0.0422862**; conservative total
reserved cost: **$0.5075062**. Unknown cost was not described as zero. No embedding
calls were made. The local worker ledger is
`docs/agent-work/spec-006-007/LIVE-BUDGET.json`.

Provider diagnostics have seven budget/scoring regression cases, exact span options
and v1 preservation have three cases, and the focused News conversation/intelligence/
adversarial/provider suite passed 47 cases. These are focused results, not the final
combined gate. SPEC-006 remains **IN PROGRESS**, with the original X, independent
quality, automated verification, full clustering/research/ranking, usability and
end-to-end completion requirements still binding.

## Bounded research and automatic relation implementation — continued 29 September

Natural timeline/background/source-comparison requests now select their intended
retrieval behavior. Timelines use retained event time or explicit publication-time
fallback and preserve that order through answer rendering. Comparison/deep retrieval
include another source before repeats within existing bounds; different sources are
not automatically independent. News chat has an answer-focus control and visible
source times. No broad open-web research or source approval is implied. 75 focused
API tests,12 PostgreSQL research tests and16 News web tests passed during development.

Automatic relation interpretation is now an optional third intelligence phase. A new
`news_evidence_relations@v1` artifact selects claim IDs, exact source spans and
SUPPORTS/CONTRADICTS/ATTRIBUTES only. A current, explicit operator-owned evidence
policy supplies the role and optional strong-evidence permission. Policies default
absent; publisher/wire original-report roles cannot be given to company/social/
research/government sources. Primary-record, correction and retraction authority is
not assigned by this model task. Exact task qualification in the gateway remains
mandatory, and no qualification or policy was created in serving configuration.
The three reserved phase ceilings sum to$0.05; the existing durable run prevents
retry spending. Current catalog fingerprints enter the context fence, and expired
or changed policies remove automatic evidence authority at reads. Original absent-
policy source fingerprint bytes and all original v1 prompt/schema digests are preserved.

The initial pipeline/adversarial/provider suite passed61 tests; PostgreSQL relation+
intelligence cohort passed34 tests. Further tests cover two explicitly clustered,
independent authored reports reaching CORROBORATED through the automatic pipeline;
that fixture does not establish semantic clustering quality. Combined final gate
still pending on the completed SPEC007 tree.

The approved OpenAI candidate was tested live on five newly authored public relation
fixtures. **4/5 passed**: the support case returned an empty relation set; contradiction,
attribution, unrelated text and prompt injection cases passed. Known cost$0.0013922,
no unknown-cost calls, reservation$0.25 under the user's$10 session cap. No retry,
source-account data, serving promotion or config change. This failure is retained in
`provider-diagnostic-relations-20260929.json` and prevents claiming qualification.
Independent corpus quality, live source configuration, calibrated semantic grouping,
importance ranking, broader research and human usability remain open.

## Scope change — 30 September 2026

The owner removed X integration, its UI and its API-access acceptance dependency
from SPEC-006. Earlier sections record the requirements and observations in force
when those tests ran; their X-related to-do statements are superseded. The retained
rumor-safety gate uses independently sourced, rights-cleared material and still
requires that popularity or repeated copies never establish claim truth.
