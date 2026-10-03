# S008-M2 implementation report — structured intent bridge and read-only email resolution

Status: **ready_for_review** (Flash worker; M2 only, not full SPEC-008)
Branch: `spec-008-navoxbot`, stacked on `bb0a7c96f4f9a7a6e9acb50fad431f8c546c6256`.
No commit, push, deployment, provider activation, paid provider call or
production migration was made. `M1-REPORT.md`, `CHECKPOINT.md`, `DESIGN.md`,
`ACCEPTANCE-MATRIX.md`, `AGENTS.md`, `.github/workflows`, deployment flags and
the untracked SPEC-006/007 material were not modified.
This revision includes the root-review correction pass: SPEC-005 envelope
validation on the runtime boundary, a no-provider fallback scoped to the
planner's explicit 503, incomplete-coverage clarification for SPEC-007 results,
and honest freshness labels.

## What now works

An unconstrained request that SPEC-002 declines (`unsupported`) is now planned by
the registered SPEC-005 gateway instead of being answered from a string match.
The TypeScript runtime validates that plan against its own capability registry,
binds any follow-up pointer to a session-owned turn, rechecks the current
authenticated account, and may delegate exactly one read-only connected search.
No route in this bundle can execute, approve, send or schedule anything.

The turn flow is:

1. `POST /today/query` with the operator's original wording (unchanged M1
   behavior, unchanged failure handling).
2. A supported SPEC-002 answer is returned as before; nothing else runs.
3. Only an `unsupported` answer proceeds to `POST /ai/assistant/intents`
   (SPEC-005) with the utterance and at most four prior **user questions**.
4. `parseIntentPlanEnvelope` unwraps the SPEC-005 response envelope — requiring
   `actions_executed` to be exactly `false`, the echoed `session_id` to match the
   SPEC-005 session this runtime asked for, a plan to exist, and no other
   envelope field — and then `parseUpstreamIntentPlan` accepts the routes and
   grounded slots, pins the operator's question, maps the route through the
   capability registry, refuses every field this runtime does not own, and binds
   a `RECENT_TURN` ordinal to a session turn id.
5. `email.search` rechecks `/auth/me` and calls read-only SPEC-007
   `POST /search/query` with `types: [EMAIL, EMAIL_THREAD]`; one match becomes a
   `READY` answer with exact citations only when coverage is complete, while
   several matches, no matches, or incomplete coverage (`truncated`,
   `unavailable_modes`, `source_issues`, `partial_reasons`) become honest
   `CLARIFY` responses with the candidate selectors and a coverage caveat.

Only one condition keeps SPEC-002's own clarification: an explicit 503 from
`/ai/assistant/intents`, which that route reserves for "no qualified AI provider
is available". The fallback is scoped to the planner call alone, so a planner
outage at any other status, a SPEC-007 search outage, or an unverifiable search
payload stays a qualified `UNAVAILABLE` with its own `plan.*`/`search.*` reason
and is never silently replaced by Today. Unknown routes, over-long or incoherent
slots, injected authority fields, a claimed action result, a session mismatch
and an altered account scope are all refused before any delegation.

## The SPEC-005 exception, and why it is bounded

`navox/ai/intent_plan.py` adds one registered artifact pair,
`assistant_intent_plan@v1` (prompt and schema, registered in
`builtin_prompts()`/`catalog_template()`), one `TaskType.PLAN` task on the
existing `PLANNING_HIGH` profile, one minimized context builder, and one
authenticated route. It reuses the existing registry, router, budget,
fallback, schema-validation and `AITaskRun` audit paths; no policy rule,
threshold, evaluation rule, provider flag or Alembic revision changed, and
published prompt/schema versions are untouched. The registry can represent this
bounded task, so the stop condition in `M2-BRIEF.md` did not apply.

**Wire contract** (`POST /api/v1/ai/assistant/intents`):

- Request: `utterance` (1–500 chars, trimmed), `recent_references`
  (≤4 items, each 1–240 chars), optional `session_id`. Unknown fields are
  rejected with 422.
- Response: `{ task_id, trace_id, plan: { version: 1, intents[1..4] },
  session_id, turn_sequence, actions_executed: false }`. Each intent carries
  `route` (`today.read` | `email.search` | `assistant.clarify`), `entity`
  (`NONE`/`PERSON`/`ORGANIZATION`/`PROJECT`/`TOPIC` + exact source text +
  confidence), `time` (`NONE`/`RELATIVE`/`ABSOLUTE`/`RANGE` + expression + trust),
  `reference` (`NONE` | `RECENT_TURN` + 1-based ordinal), `confidence`,
  `requires_clarification` and `clarification`. There is no tool URL, provider,
  model, recipient, approval, grant or executable argument anywhere in the
  response, and there is no field for one.
- The runtime consumes that envelope, not a bare plan: `actions_executed` must
  be exactly `false` (missing, `null`, `"false"` and `true` are all refused),
  `session_id` must equal the SPEC-005 session id this runtime requested,
  `task_id`/`trace_id` must be UUIDs, `turn_sequence` must be `null` or a
  non-negative integer, `plan` must exist, and any other envelope field is a
  refusal. `reason` on the internal error type carries the one condition the
  runtime must react to and is never serialized to a client.
- Failures: 401 unauthenticated, 403 scope/permission, 409 session changed or
  not owned, 422 bounded-input violation, 503 no qualified provider or an
  unvalidatable model output.

## Changed paths

Python (`services/api`, the stated existing-dependency exception):

- `navox/ai/intent_plan.py` (new): route/slot contracts, strict JSON schema,
  prompt instructions, minimized planning context, registered `PLANNING_HIGH`
  task, `plan_intents()` service.
- `navox/ai/prompts.py`: registers `assistant_intent_plan` v1.
- `navox/api/ai_operations.py`: `POST /ai/assistant/intents`.
- `tests/test_ai_intent_plan.py` (new): 14 focused tests.

TypeScript:

- `packages/contracts/src/assistant.ts`: `email.search` capability/intent,
  entity/time/reference slot contracts, extended `PlannedIntent`.
- `packages/assistant-runtime/src/validate.ts`: slot validators, fail-closed
  `turn_id` handling, clarification coherence, legacy-plan defaults.
- `packages/assistant-runtime/src/capabilities.ts`: `email.search`
  (`knowledge.search`, read-only), route→capability map, integrity assertions.
- `packages/assistant-runtime/src/planner.ts`: `parseUpstreamIntentPlan`,
  `bindRecentTurnReference`.
- `packages/assistant-runtime/src/email.ts` (new): SPEC-007 response validation
  and the single/ambiguous/empty read-only decisions.
- `packages/assistant-runtime/src/gateway.ts`: `planIntents`, `searchEmail`,
  qualified planner/search status mapping.
- `packages/assistant-runtime/src/runtime.ts`: the unsupported-request bridge,
  route resolution, scope recheck, refusal mapping.
- `packages/assistant-runtime/src/limits.ts`, `index.ts`,
  `src/testing/fakes.ts`, `src/store.ts` (stored plans may carry a bound turn),
  and the focused unit suites (`planner`, `email`, `runtime`, `gateway`,
  `validate`, `capabilities`, `store.integration`).
- `apps/web/**` needed no change: the existing Next `/api/v1/assistant/*`
  handlers call `submitTurn`, which now owns the bridge. The web lint,
  typecheck, tests and build were rerun on the final tree.

## Boundary decisions

- **No keyword routing.** The runtime never inspects the question text. SPEC-002
  classifies first; only its own `unsupported` answer reaches the planner, and
  the planner's route must exist in the server-owned registry.
- **A plan is data, not authority.** The upstream payload is `unknown` until
  validated. Extra fields (`workspace_id`, `user_id`, `capability_id`,
  `tool_name`, `action_grant`, …) are refused, and an upstream plan may not
  supply its own session turn selector — only this runtime may bind one.
- **Scope is rechecked.** The account is re-resolved from the forwarded cookie
  immediately before delegation to another owning service; a changed
  user/workspace yields `WITHHELD` with no downstream call. Every local read and
  write stays fenced by session + workspace + user.
- **Read-only only.** `email.search` is `mode: "read_only"`,
  `requires_approval: false`; a route that resolved to a consequential
  capability is refused before delegation. There is still no action endpoint,
  no draft, no approval and no send in this bundle.
- **Compound plans clarify.** A plan with more than one intent is answered with
  a clarification and executes nothing; compound synthesis stays in the later
  phase defined by `DESIGN.md`.
- **No source text leaves or is copied.** Only the operator's own prior
  questions are sent as follow-up references — never another service's answer
  text. SPEC-007 excerpt text is counted, never rendered or persisted; items
  keep titles, currentness (`CURRENT`/`STALE`/`UNVERIFIED`) and exact selectors
  (resource id, connection id, external resource id, source version/time).
- **Honest failures.** No planner, a disabled connected search, an unreadable
  upstream payload, a search outage and a model output outside the route
  vocabulary all produce qualified `UNAVAILABLE`/`WITHHELD`/`CLARIFY` responses
  with `action_state: "NONE"` and `actions_executed: false`. SPEC-002's own
  clarification is used only for the planner's explicit no-provider 503.
- **Incomplete coverage is not a unique target.** When SPEC-007 reports
  truncated results, unavailable modes, source issues or partial coverage, even
  a single candidate is a `CLARIFY` (`email.search.incomplete`) with the
  candidate, the selectors and a coverage caveat — never a `READY` match.
- **Freshness follows the owning metadata.** An item is `CURRENT` only inside
  the source's own `fresh_until` window; an absent or unparseable `fresh_until`,
  or an absent `source_version`, is `UNVERIFIED`, and a past window is `STALE`.

## Verification

All commands were run from `/Users/mba_steins/NavoX` on the final working tree.

| Command | Exit | Result |
| --- | --- | --- |
| `bash scripts/check-api.sh` with the disposable PostgreSQL/Temporal stack (`NAVOX_CONNECTOR_TEST_DSN=postgresql+asyncpg://navox:navox@localhost:15433/navox`, `NAVOX_TEMPORAL_TEST_TARGET=localhost:17234`) | 0 | **2546 passed, 0 skipped** in 16m49s; Ruff lint, Ruff format, strict mypy, Alembic revision width, full suite, SPEC-003 metric thresholds, SPEC-004 thresholds, Alembic SQL/required schema, deterministic release evaluation and patch whitespace all PASS. Log: `/tmp/navox-spec008-m2-api-gate-pg.log` |
| `bash scripts/check-api.sh` without the stack DSN | 1 | 2538 passed, **8 skipped**; only the two measurement gates failed, and they fail on any skip. Those eight tests require the PostgreSQL row-lock/Temporal stack (`test_knowledge_ask` ×3, `test_news_semantic_clustering` ×3, `test_knowledge_semantic` ×1, `test_knowledge_temporal` ×1). Log: `/tmp/navox-spec008-m2-api-gate.log` |
| `npm run lint` (root) | 0 | runtime 39 files clean; web 135 files with the 10 pre-existing CSS warnings only |
| `npm run typecheck` (root) | 0 | contracts, runtime, web, connector-sdk and ui clean |
| `npm run test` (root) | 0 | web 275 passed; runtime 155 passed, 5 skipped (no DB); extension suite ran |
| `NAVOX_ISOLATED_BUILD=1 npm run build --workspace=@navox/web` | 0 | compiled; `/navox` prerendered; three `ƒ` assistant API routes |
| `ASSISTANT_TEST_DATABASE_URL=… npm run test --workspace=@navox/assistant-runtime` | 0 | **160 passed, 0 skipped** |
| `ASSISTANT_TEST_DATABASE_URL=… npm run migration:check --workspace=@navox/assistant-runtime` | 0 | **29 assertions** against live PostgreSQL; migration still does not alter the Alembic head |

New coverage in this bundle: SPEC-005 route registration and the plan contract
(unknown route, incoherent clarification, five-intent plan, injected
`action_grant`), authenticated plan with `AITaskRun` audit
(`assistant_intent_plan@v1`, `PLANNING_HIGH`), input bounds and 401/422 paths,
no-provider 503 with no adapter call, model output outside the route vocabulary,
credential-like utterance blocked before the model, foreign session refused;
TypeScript unknown-route/extra-field/`turn_id`/workspace/ordinal/slot refusals,
legacy stored-plan defaults, route→capability mapping, follow-up ordinal bound to
a session turn id, SPEC-007 parsing (unknown type, bad selector, bad coverage),
single/ambiguous/empty email outcomes, stale/unverified/truncated reporting,
excerpt text never rendered, compound-plan clarification, scope recheck before
delegation, disabled/forbidden search, and zero `action_refs` in every planning
outcome.

The correction pass additionally covers: envelope unwrapping and binding from
the real Python response shape, `actions_executed` missing/`null`/`"false"`/
`true`, session mismatch (including a null echo), a missing plan, invalid
`task_id`/`trace_id`/`turn_sequence`, envelope-level workspace/user/tool/grant
injection, an upstream-supplied turn selector; the real wire shape end to end
through `createNavoxUpstream` → runtime → SPEC-007 payload; the planner
no-provider 503 fallback versus a non-503 planner outage; a SPEC-007 503 and a
malformed search payload staying `search.unavailable`; and single-candidate
truncated/unavailable-mode/source-issue/partial-coverage results becoming
`email.search.incomplete` clarifications.

## Risks and limitations

- **No live provider run.** The planner path is exercised with fixtures and the
  registered catalog; no paid or live model call was made, and the deployed
  default remains provider-less. Plan quality against real wording is
  unmeasured — that is the SPEC-008 hardening phase, not this bundle.
- **The bridge is only as good as the registry.** A deployment must publish a
  catalog containing `assistant_intent_plan@v1` and assign a qualified model to
  `PLANNING_HIGH`; otherwise the endpoint returns 503 and the turn falls back to
  SPEC-002's clarification. Publication is an operator action and was not
  performed here.
- **Compound and cross-domain turns are still open.** More than one intent is a
  clarification in M2.
- **Email read-only resolution is bounded to connected search.** It needs the
  SPEC-007 knowledge feature enabled; when it is off the turn is an honest
  `UNAVAILABLE` rather than a fabricated match. Excerpts are deliberately not
  rendered, so the user sees titles, selectors and currentness rather than mail
  bodies — the draft/approval bundle owns richer, authorized presentation.
- **Follow-up references describe prior questions only.** Answer text from other
  services is not forwarded to the planner, so a follow-up that depends purely
  on a previous answer's wording needs the later reference-resolver work.
- **Hosted CI was not run.** This branch has no commit or push, so the required
  hosted CI on an exact head SHA remains outstanding, as does the Compose
  integration check.

## Decisions for Astra

1. Confirm the planner route vocabulary (`today.read`, `email.search`,
   `assistant.clarify`) as the frozen SPEC-005 contract for the next phase, and
   the `RECENT_TURN` ordinal semantics (1-based, oldest-first over the four most
   recent user questions).
2. Decide whether the next bundle should render authorized SPEC-007 excerpt text
   (with its retention implications) or continue selector-only presentation
   until the draft/approval flow exists.
3. Decide when an operator catalog publication with `assistant_intent_plan@v1`
   plus a `PLANNING_HIGH` assignment should be prepared for a provider-qualified
   environment, since M2 intentionally activates nothing.

## Remaining SPEC-008 phases

Still open: bounded multi-intent synthesis, SPEC-007 evidence selectors feeding an
authorized draft, exact SPEC-001/003 approval/execution/verification for email
and meeting preparation, subscriptions, Calendar/class navigation, News, general
reasoning, realtime speech transport and barge-in tuning, a live wake adapter,
bounded activity/memory, TypeScript Temporal workflows, and the
hardening/measurement phase with hosted CI and human acceptance evidence.
