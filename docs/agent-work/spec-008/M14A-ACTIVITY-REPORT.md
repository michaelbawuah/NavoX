# SPEC-008 M14A - bounded personal Activity (R3 scenario O)

**Status: local implementation and full pre-push gate passed; hosted acceptance
pending.** This slice adds a read-only Activity answer that reports the
operator's recent action-ledger records honestly. No provider call, action
execution, approval, new authority, Temporal/workflow edit, config change,
commit or push was made during worker implementation. Commit/push and
exact-head CI remain root-owned.

## What changed

- `packages/contracts/src/assistant.ts`: added `action.history` to
  `AssistantCapabilityId` and `AssistantIntentKind`.
- `packages/assistant-runtime/src/validate.ts`: added `action.history` to
  `CAPABILITY_IDS` and `INTENT_KINDS`, so an unknown route or capability stays
  rejected at the boundary.
- `packages/assistant-runtime/src/capabilities.ts`: registered `action.history`
  as a `read_only`, `requires_approval: false` capability whose
  `delegate_target` is `actions.query`, and mapped the route to it.
- `packages/assistant-runtime/src/gateway.ts`: added
  `NavoxUpstream.listActions(cookie, { limit })`, which reads the existing
  authenticated SPEC-001/003 `GET /api/v1/actions?limit=50`. The route is
  already scoped by workspace and user; the runtime never duplicates action
  logic, never POSTs, and never re-implements approval.
- `packages/assistant-runtime/src/activity.ts` (new): strict source parser,
  event-time resolution, status classification, local-day resolution and the
  bounded answer builder.
- `packages/assistant-runtime/src/runtime.ts`: resolves `action.history` after
  the account recheck and before the email fallback; added the `Activity`
  compound label. Clarifies unsupported selectors, qualifies upstream failures
  as `UNAVAILABLE` through the existing `planFailure` path.
- `packages/assistant-runtime/src/testing/fakes.ts`: added a `listActions` fake.
- `packages/assistant-runtime/src/activity.test.ts` (new): 20 focused tests.
- `services/api/navox/ai/intent_plan.py`: added
  `IntentRoute.ACTION_HISTORY = "action.history"`, froze the previous
  instruction block as `INTENT_PLAN_INSTRUCTIONS_V7`, added the `action.history`
  route description to the active prompt, moved `INTENT_PLAN_PROMPT`/
  `INTENT_PLAN_SCHEMA` to `assistant_intent_plan@v8`, and added
  `intent_plan_json_schema(legacy_v7=True)` so every historical artifact
  (v1-v7) excludes the new route.
- `services/api/navox/ai/prompts.py`: publishes the frozen v7 artifact and the
  new v8 artifact. Published v1-v7 content is unchanged.
- `services/api/tests/test_ai_intent_plan.py`: catalog/planner contract tests
  updated for v8, plus assertions that v1-v7 never name `action.history`.

## Behavior against the acceptance contract

Routing. `What did you do today?` reaches the SPEC-005 planner like any other
free-form turn. The runtime only reads the structured route and slots the
planner returns; there is no fixed-phrase shortcut and no keyword router. An
`action.history` intent with no entity and no time expression lists the recent
records; a literal `today` time slot resolves the local day. A named entity
(for example a person) or an unresolved date returns `CLARIFY` without
contacting the ledger.

Source and scope. The only source is the existing authenticated
`GET /api/v1/actions`, at the endpoint's own 50-row maximum. The parser binds
only `id`, `action_type`, `provider`, `status`, `created_at`, `executed_at` and
`verified_at`. `payload`, `result`, recipients, bodies and token-like fields
are never read, bound or rendered. A row that names another `user_id` or
`workspace_id` is refused; duplicated ids, over-limit pages, non-UUID ids,
non-string statuses and unparsable timestamps are all qualified failures rather
than answers. An impossible verification is refused too: a `verified_at` on a
non-completed row, a verified row with no `executed_at`, or a `verified_at`
that precedes the `executed_at` it claims to verify. The existing contracts
(`gmail.send`, `subscription.cancel`) always write the execution time before
the verification time, so a row that contradicts that ordering is not proof of
anything and fails closed.

Verification honesty. Only a `completed` row with a non-null `verified_at` is
described as done (`VERIFIED`). A `completed` row without verification is
`EXECUTED_UNVERIFIED` ("executed, not independently verified"). Pending
approval, in progress, failed, declined, cancelled, expired, uncertain,
blocked and unrecognized statuses each get their own label straight from the
live ledger status. Age never overrides a status: a record that has been
awaiting approval for more than a day is still reported as awaiting approval,
with a separate sentence stating that the age note is informational and does
not change the ledger status. The answer's summary sentence counts the buckets
and states that only independently verified actions are described as done.

Event time and time. The day filter, the item `observed_at` and the age note
all use the same source-backed event time: a verified action is reported on its
`verified_at`, a completed-but-unverified action on its `executed_at`, and any
other record on its latest recorded transition (`executed_at` when present,
otherwise `created_at`). An action prepared the previous day but verified today
therefore belongs to today, and one created today but verified the next day
belongs to the verification day. `today` is resolved from the request's
validated IANA timezone with an explicit local-date label in both answer forms;
a day-scoped request with a missing or unusable timezone is refused with
`CLARIFY` instead of silently answering from UTC.

Bounds and provenance. At most 50 records are requested; at most 12 are
rendered as items, with an explicit "showing the 12 most recent of N" line. A
full 50-row page is reported as possibly partial, because the endpoint returns
no total. Each item carries a `navox`/`ACTION` citation whose `evidence_id` is
the action id and whose `observed_at` is that same event time. The same
`AssistantSession` and turn are reused; the capability is
`requires_approval: false`, `action_state: NONE`, and appends no action ref.

## Verification

TypeScript (`packages/assistant-runtime`):

- `npx tsc --noEmit -p packages/assistant-runtime/tsconfig.json` - exit 0.
- `npx tsc --noEmit -p packages/contracts/tsconfig.json` - exit 0.
- `npx biome check .` from `packages/assistant-runtime` - exit 0, no fixes.
- `npx vitest run --root packages/assistant-runtime` - 288 passed, 5 skipped
  (the 5 skips are the PostgreSQL-only store integration cases).
- `npm run migration:check --workspace=@navox/assistant-runtime` - passed 19
  assertions; the live schema check was skipped because
  `ASSISTANT_TEST_DATABASE_URL` is not set.

Repository JavaScript (`/Users/mba_steins/NavoX`):

- `npm run lint` - exit 0 (9 pre-existing `noDescendingSpecificity` warnings in
  `apps/web` CSS, no errors).
- `npm run typecheck` - exit 0 for web, assistant-runtime, connector-sdk,
  contracts and ui.
- `npm run test` - Web 386 passed, extension 8 passed, assistant-runtime 288
  passed with 5 PostgreSQL-only skips.
- `npm run build` - exit 0 (isolated Next production build).

Python (`services/api`, `UV_CACHE_DIR=/private/tmp/navox-uv-cache`,
`uv run --no-sync`):

- `python -m pytest tests/test_ai_intent_plan.py -q` - 17 passed.
- `python -m pytest tests/test_ai_intent_plan.py tests/test_ai_registry.py
  tests/test_ai_readiness.py tests/test_approvals.py -q` - 41 passed.
- `python -m pytest -q` (full suite, corrected tree on top of `cbe7dbd`) -
  2,718 passed, 0 failed, 8 skipped in 470s. The 8 skips are the
  PostgreSQL-only cases. An earlier run on the pre-repair tree had one
  wall-clock-dependent fixture failure in
  `tests/test_knowledge_api.py::test_search_returns_nonempty_news_evidence_through_the_owned_service`;
  it reproduces identically in a pristine `git archive 2b65fff` copy, is
  unrelated to M14A, and was repaired by the root commit `cbe7dbd`.
  `python -m pytest tests/test_knowledge_api.py::test_search_returns_nonempty_news_evidence_through_the_owned_service tests/test_ai_intent_plan.py -q`
  passes 18/18 on the current HEAD.
- `python -m ruff check` and `python -m ruff format --check` on
  `navox/ai/intent_plan.py`, `navox/ai/prompts.py` and
  `tests/test_ai_intent_plan.py` - exit 0.
- `python -m mypy navox` - success, 281 source files.

Failing-first evidence. The first run of the updated catalog test failed with
`assert 'time.now' not in [...v7 routes...]`, showing the v7 artifact correctly
retains the routes it published; the test was corrected to assert what v7
actually freezes. The first run of the timezone-boundary test also failed
because the chosen instant was still inside the UTC day; the fixture was moved
to `2026-09-30T22:00:00.000Z`, which is 30 September in `America/New_York` but
30 September UTC, and now proves the boundary. The rest of the suite was
written after the implementation, so it is regression evidence rather than
strict red/green.

## Correction cycle 1 (Astra review)

1. Day filtering now uses the action's source-backed event time rather than its
   creation time. `actionEventAt(record)` returns `verified_at` for a verified
   completion, `executed_at` for a completed-but-unverified one, and the latest
   recorded transition (`executed_at`, else `created_at`) for every other
   status. The day filter, the item `observed_at` and the age note all use it.
   Red/green: with the filter temporarily reverted to `created_at`, the new
   regression failed with
   `expected "1 verified as done"` / received
   `"No actions were recorded today (Wednesday, September 30, 2026,
   America/New_York)"`; with the event time restored, both regressions pass.
2. A verified row must carry the execution it verifies. The parser now refuses
   a `completed` row whose `verified_at` has no `executed_at`, and one whose
   `executed_at` is later than its `verified_at`, matching the existing
   `gmail.send` and `subscription.cancel` contracts, which always record the
   execution before the verification. Red/green: with the chronology check
   temporarily removed, the new case failed with "expected to throw"; with it
   restored the row becomes a qualified `UNAVAILABLE`.
3. The invented 24-hour `STALE` classification is gone. `classifyAction` maps
   the ledger status only, so a `pending`/`awaiting_approval` row older than a
   day is still `PENDING_APPROVAL` and an `approved`/`executing` row is still
   `IN_PROGRESS`. Age survives only as a sentence in the answer text that says
   the age note is informational and does not change the ledger status. The new
   regression asserts both the unchanged status label and that non-authoritative
   wording, and that the answer never contains the word "stale".

## Final root review

The owning action endpoint orders its 50-row page by creation time. The
runtime now orders the returned records by source-backed event time before
showing its 12 visible items, so a recent verification of an older action
appears before an action merely created later. The 50-row source bound still
means the overall activity history may be partial, as the answer states.
The parser also refuses a `completed` row with no `executed_at`, even when it
has no verification timestamp; such a row cannot support the phrase
"executed, not independently verified." Both cases have focused regressions.
Root-focused Vitest passes 21/21 and repository TypeScript typecheck passes.
The full API pre-push gate passed with 2,726 tests, zero skips, plus lint,
types, schema/metric/release checks and patch whitespace. Root npm lint,
typecheck, tests and production Web build passed; the PostgreSQL-backed
runtime passed 294/294 and the live migration check passed 29 assertions.
The gate log is `/tmp/navox-spec008-m14a-root-api-gate.log`.

Baseline CI. Hosted CI run `36858921683` (run 215) on baseline
`2b65fff7097ae03e90eebef5fbb8b5e3133a4b10` was still `in_progress` when this
report was written. Web quality, Chrome extension quality and Dependency
security had completed successfully; API quality had passed Ruff, Ruff format
and strict mypy and was mid-`pytest`. No failed check was observed, so feature
work continued as instructed. The root CI repair was then committed as
`cbe7dbda54161eee5e54eeae4bdf97272af86631` (it pins the News search fixture
clock, which is the failure recorded below). Run `36869205769` (run 216) on
that head was still `in_progress` at the end of this cycle, with no failed
check observed.

## Limitations and risks

- No live planner, model or action ledger was called. `action.history` only
  appears when an operator publishes `assistant_intent_plan@v8`; a deployment
  still pointing at `@v7` will not emit the route. Catalog publication is an
  explicit operator action and was not performed.
- The runtime reads the endpoint's first 50 rows, newest first, and states that
  a full page may be partial. There is no paging or total count in the existing
  endpoint, so the answer cannot report an exact total.
- Age is informational only. A non-terminal record that has been open for more
  than a day keeps its own ledger status and gains one explicit, non-authoritative
  sentence; the runtime never invents a status from elapsed time.
- A verified row with an impossible chronology is refused rather than shown.
  Because the existing contracts always write `executed_at` before
  `verified_at`, a row that violates that order is reported as qualified
  `UNAVAILABLE`; if a future action contract gains a different verification
  shape, this parser has to be revisited with that contract.
- `rejectForeignScope` checks a `user_id`/`workspace_id` field only when the
  row exposes one. The authoritative scoping remains the API's own
  authenticated query; this is defense in depth, not a substitute for it.
- The full API pre-push gate with PostgreSQL/Temporal, hosted CI, and any live
  end-to-end observation of scenario O remain outstanding and are root-owned.

## Decisions for Astra

- The capability ID, intent route and delegate target are all `action.history`
  -> `actions.query`; the planner artifact moves to `@v8` and v1-v7 stay
  byte-frozen. If a different naming is preferred for the catalog, it needs to
  change before publication.
- Activity is a read-only route, so it is not part of the approval/Temporal
  surface and adds no action refs. Any future write or retry behavior would be
  a separate bundle.
