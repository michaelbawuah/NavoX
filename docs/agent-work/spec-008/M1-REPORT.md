# S008-M1 implementation report — TypeScript runtime vertical slice

Status: **ready_for_review** (Flash worker; M1 only, not full SPEC-008)
Branch: `spec-008-navoxbot`, stacked on `bb0a7c96f4f9a7a6e9acb50fad431f8c546c6256`.
No commit, push, deployment, activation, paid provider call or production
migration was made. This revision includes the Astra acceptance corrections:
trust-boundary tightening, durable per-request claims, client session
generation, cancel semantics, click-to-speak, Today intent pinning, honest
privacy/retention wording, and the root-owned deployment env names.

## What now works

One authenticated assistant session covers typed and clicked-microphone turns.
The TypeScript API derives the account from the forwarded HttpOnly cookie via the
configured NavoX `/auth/me`, persists bounded NavoXbot rows keyed to the SPEC-005
`assistant_sessions` identity, and answers read-only Today questions by calling
the existing SPEC-002 `/today/query` with the operator's original wording.
SPEC-002 still owns classification and the current projection; `unsupported`
becomes an honest `CLARIFY`, and any upstream failure becomes `UNAVAILABLE` with a
notice block instead of a fabricated answer. No model provider SDK is called from
NavoXbot, and M1 exposes no consequential action route.

## Changed paths

- `packages/contracts/src/assistant.ts` (new, type-only), `packages/contracts/src/index.ts`
- `packages/assistant-runtime/**` (new package: `src/{index,limits,errors,validate,capabilities,planner,today,presentation,voice,ledger,http,gateway,store,db,runtime,purge}.ts`,
  `src/testing/fakes.ts`, 13 unit suites + 1 gated integration suite,
  `migrations/0001_assistant_runtime.sql`, `scripts/migrate.mjs`, `scripts/migration-check.mjs`,
  `package.json`, `tsconfig.json`, `biome.json`)
- `apps/web/src/app/api/v1/assistant/sessions/route.ts` (POST create)
- `apps/web/src/app/api/v1/assistant/sessions/[sessionId]/route.ts` (GET, DELETE)
- `apps/web/src/app/api/v1/assistant/sessions/[sessionId]/messages/route.ts` (POST turn)
- `apps/web/src/lib/assistant-{server,route,client,controller,speech}.ts` plus their four test files
- `apps/web/src/app/navox/page.tsx`, `apps/web/src/components/navox-assistant.tsx`, `navox-assistant.module.css`, `navox-assistant.test.tsx`
- `apps/web/src/components/navox-ui.tsx` (navigation link), `package-lock.json` (workspace + `pg`/`@types/pg`)

`docs/agent-work/spec-008/DESIGN.md`, `M1-BRIEF.md`, `CHECKPOINT.md` and every
Python/Alembic/workflow file were left untouched. Root's in-flight `.env.example`,
`.github/workflows/ci.yml`, `apps/web/Dockerfile`, `docker-compose.yml` and
`.dockerignore` edits are preserved, and the migration command root wired into
Compose (`npm run migrate --workspace=@navox/assistant-runtime`) was matched by
teaching the scripts the `NAVOX_ASSISTANT_DATABASE_URL` name they set.

## Boundary decisions

- **Contracts stay type-only.** `@navox/contracts/src/assistant.ts` exports types
  only, matching the package convention; every executable validator lives in the
  runtime package and rejects unknown route, capability, tool, block, action and
  response values.
- **Client authority is refused, not ignored.** A body carrying `workspace_id`,
  `user_id`, `provider`, `tool_name`, `action_grant` or `trusted_source` fails
  validation; scope always comes from `/auth/me` and every local read/write is
  fenced by session + workspace + user.
- **Same-origin writes compare full origins.** `assertSameOriginMutation` requires
  an exact `scheme://host[:port]` match against `NAVOX_ASSISTANT_ORIGIN` when it is
  configured, otherwise against the server-derived request URL. Malformed values,
  `Origin: null`, non-http schemes and same-host scheme/port mismatches are all
  refused, and a client-supplied `Host`/`X-Forwarded-Host` header is no longer an
  authority fallback.
- **No keyword router.** `planTurn` emits exactly one `today.read` intent and
  carries the original text to SPEC-002; "Delete all my files" still routes to
  read-only Today and is classified by the owning service.
- **Today's answer is pinned and must be internally consistent.**
  `parseTodayQueryResult` accepts only the SPEC-002 `QueryIntent` vocabulary
  (`today`, `attention`, `this_week`, `waiting`, `renewals`, `promises`,
  `forgetting`, `meeting_prep`, `handleable`, `unsupported`). An unknown intent,
  or a supported intent with empty answer text, is a qualified `UNAVAILABLE`
  failure — never an assumed `READY`. When Today returns nothing to show, the
  notice state matches `decideToday` instead of a blanket `UNAVAILABLE`.
- **Retention and bounding in the database and the code.** 30-day `expires_at`
  with a CHECK bound, 500-character questions, 4000-character answers,
  64-character request fingerprints, ≤200 turns per session, ≤2000 sequence
  backstop, no audio and no third-party evidence text (citations keep
  selectors/IDs only).
- **Durable per-request claims.** A claim row (`assistant_runtime_requests`, keyed
  by session + request) is written *before* the upstream call. A concurrent
  duplicate waits for the owner and replays one result; a changed payload behind
  the same request ID is a `409 conflict` before any Today call; a claim older
  than the stale window (30 s, longer than the 10 s upstream timeout) can be taken
  over; and a failure that stores nothing releases the claim so a retry can
  proceed.
- **Voice is click-to-talk and inert beyond the page.** `reduceVoiceState` owns
  listening/transcribing/thinking/speaking/stopped; a new utterance interrupts
  TTS; typed turns never speak by themselves; unsupported browsers get a typed
  fallback. Every eligible answer turn carries an accessible Speak control, so a
  typed answer can be replayed on demand without ever speaking unasked.
  `stopListening()` uses `abort()` semantics and discards a buffered transcript,
  so an explicit Stop never submits partial speech. `WakeWordAdapter` exists with
  an inert implementation (`supported: false`, `start()` throws). No detector is
  registered, and nothing starts a microphone except the operator pressing the
  control.
- **Late completions cannot leak.** The client turn runner keeps a session
  generation; clearing the conversation or unmounting invalidates it, stops
  speech and listening, and drops any completion from the older generation so a
  stale answer is never appended or spoken.

## Privacy and retention boundaries (honest limits)

- **Browser speech can leave the device.** The microphone path uses the browser's
  own `SpeechRecognition` and `speechSynthesis`. Browser vendors may implement
  `SpeechRecognition` with a remote vendor service, so M1 does **not** claim
  on-device processing or that no third-party/provider traffic occurs on the voice
  path. This is an unresolved production acceptance boundary for SPEC-008: any
  claim of on-device or SPEC-005-routed speech needs a real transport measurement
  and an explicit product decision, not an assumption. NavoXbot itself opens no
  model SDK and sends typed questions only to the configured NavoX API.
- **30 days is an access bound with a scheduled cleanup, not an exact deletion
  deadline.** Sessions expire 30 days after creation, a bounded purge
  (`purgeBatchSize` 500 rows per pass, non-overlapping, unref'd timer, cadence
  `NAVOX_ASSISTANT_PURGE_INTERVAL_MS`, default 6 h) runs in the owned TS path while
  the Node server is up, and each session create also attempts one opportunistic
  pass. Dormant rows in a server that is not running are removed on the next
  successful pass, so the UI and this report state expiry plus pending physical
  purge rather than a guaranteed deletion time.

## Migration status

The NavoXbot schema is TypeScript-owned and deliberately outside the Alembic
head; no Python file or revision changed. Apply with
`npm run migrate --workspace=@navox/assistant-runtime` (reads
`NAVOX_ASSISTANT_DATABASE_URL`, then `ASSISTANT_DATABASE_URL`, then
`DATABASE_URL`) and verify with
`npm run migration:check --workspace=@navox/assistant-runtime`. That check runs
19 deterministic assertions without a database and adds catalog assertions
(three tables, foreign keys to `assistant_sessions`/`workspaces`/`users`, the
claims primary key, unique constraints, check constraints) when
`ASSISTANT_TEST_DATABASE_URL` or `NAVOX_ASSISTANT_DATABASE_URL` is set. It
  verified **29 assertions against a real PostgreSQL 17** in this run. Root
  subsequently added the separate schema and Compose checks to CI.

## Verification

| Command | Exit | Result |
| --- | --- | --- |
| `npm run typecheck --workspace=@navox/contracts` | 0 | clean |
| `npm run lint --workspace=@navox/assistant-runtime` | 0 | 36 files, 0 errors |
| `npm run typecheck --workspace=@navox/assistant-runtime` | 0 | clean |
| `npm run test --workspace=@navox/assistant-runtime` | 0 | 109 passed, 4 skipped (no disposable DB) |
| `npm run migration:check --workspace=@navox/assistant-runtime` | 0 | 19 assertions passed, live check skipped |
| `ASSISTANT_TEST_DATABASE_URL=… npm run migration:check` | 0 | **29 assertions** against disposable PostgreSQL 17 |
| `ASSISTANT_TEST_DATABASE_URL=… npm run test --workspace=@navox/assistant-runtime` | 0 | **113 passed, 0 skipped** |
| `npm run lint --workspace=@navox/web` | 0 | 135 files, 0 errors (10 pre-existing CSS warnings) |
| `npm run typecheck --workspace=@navox/web` | 0 | clean |
| `npm run test --workspace=@navox/web` | 0 | 275 passed (232 before this work) |
| `NAVOX_ISOLATED_BUILD=1 npm run build --workspace=@navox/web` | 0 | compiled; `/navox` prerendered; three `ƒ` assistant API routes |
| `npm run lint` / `typecheck` / `test` at root | 0 | all workspaces incl. extension; 275 + 109 tests pass |

Real-server evidence (`next start` on 127.0.0.1, built output):

- `GET /navox` → 200 with `Ask about your day`, the question field,
  `Start listening`, `Clear conversation` and the retention/privacy copy.
- Same-origin guard: exact origin passes the guard (then 503 for the unconfigured
  database); `https://localhost:3213` (same host, upgraded scheme) → 403;
  `http://localhost:9999` (same host, other port) → 403; `Origin: null` → 403;
  missing `Origin` → 403; cross-host with `X-Forwarded-Host: evil.example` → 403.
- With `NAVOX_ASSISTANT_ORIGIN=https://app.navox.test`: that exact origin passes
  the guard, while `http://localhost:3214` and `http://app.navox.test` → 403.
- `GET /api/v1/assistant/sessions/{id}` and `POST …/messages` without a cookie → 401.
- `node scripts/migrate.mjs` with no configured URL exits 1 with a clear message;
  with `NAVOX_ASSISTANT_DATABASE_URL` set it proceeds to connect (proving the
  Compose env name resolves).

Correction-specific tests: unknown Today intents and empty supported answers fail
closed; READY never coexists with an UNAVAILABLE notice; two concurrent
duplicates of a delayed request call Today once, converge on one turn and one
RESOLVED claim (in memory **and** against real PostgreSQL); a changed payload for
an in-flight ID conflicts before any second Today call; a failed owner releases
its claim; a stale claim is taken over; a cleared session drops a late answer and
never speaks it; Stop cancels listening without submitting a buffered transcript;
the Speak control renders only for eligible answer turns; and the request-ID
fallback produces a UUID the runtime's own validator accepts.

Disposable-database work used its own throwaway `postgres:17-alpine` containers on
`127.0.0.1:55433`/`55434` (Alembic head applied there first, containers removed
afterwards). No owner database or running owner container was read or modified.

## Risks and limitations

- **No browser tooling in this worker.** `mcp__node_repl__js` (in-app browser) is
  not in this session's tool list, so mic/TTS interaction was verified through the
  adapter, controller and state-machine suites plus real server-rendered checks,
  not a click-driven browser session. Human voice QA is still owed.
- **Browser speech privacy is unresolved** (see above): vendor-remote
  `SpeechRecognition` is a production acceptance item, not a solved property.
- **Physical purge needs a live process.** The scheduled purge only runs while the
  Node server is up; a dormant deployment keeps expired rows until the next start
  or request.
- **Same-origin baseline.** Without `NAVOX_ASSISTANT_ORIGIN`, the guard compares
  against the server-derived request URL, which can be the internal bind host
  behind a proxy (verified locally: `next start` reports `localhost`). Root's
  Compose/env wiring sets `NAVOX_ASSISTANT_ORIGIN=http://localhost:3000`.
- **Upstream SPEC-005 session rows are not deleted.** `/ai/sessions` exposes no
  delete endpoint, so "clear conversation" removes NavoXbot rows and turns only;
  deleting the SPEC-005 identity would need that owner's contract.
- **`pg` is a new runtime dependency** (lock updated, `npm audit` reported 0
  vulnerabilities). It is server-only and stays out of the client bundle; the
  client imports the `./voice` subpath, which carries no Node built-ins.

## Decisions for Astra

1. Add the separate NavoXbot schema check to CI (`.github/workflows` is outside
   this brief) so the TS-owned migration is gated like the Alembic checker.
2. Decide the speech-privacy boundary: accept browser-vendor remote recognition
   for M1, or move to an owned/SPEC-005-routed transport before any claim of
   on-device processing.
3. Confirm `NAVOX_ASSISTANT_ORIGIN`, `NAVOX_ASSISTANT_DATABASE_URL` and the purge
   cadence for production, and whether physical purge needs a scheduler outside
   the web process.
4. Confirm the SPEC-005 session-row lifetime expectation for "clear conversation".

## Remaining SPEC-008 phases

M1 is the vertical slice only. Still open: structured SPEC-005 intent bridge,
bounded multi-intent/reference resolution, SPEC-007 evidence selectors, email
draft with exact SPEC-001/003 approval/execution/verification, meeting prep, other
domains (subscriptions, calendar/class, News, general reasoning), realtime speech
transport and barge-in tuning, live wake adapter, bounded activity/memory,
TypeScript Temporal workflows, and the hardening/measurement phase. Hosted CI
remains red at the parent head for the runner-scheduling reason root diagnosed;
this branch has no push or PR.

## Acceptance-matrix touchpoints (not acceptance claims)

Per `ACCEPTANCE-MATRIX.md`, this work supplies candidate evidence only. Scenario
**L** (typed input stays silent) is covered by construction and by the
presentation/controller suites; the Speak control is an explicit operator action.
Scenario **B** (Today briefing by voice with visual detail in one session) is
implemented — one session, spoken answer and structured blocks from the same turn
— but the required observation needs a real device/human pass this worker could
not run. Scenario **N** (qualified provider/source failure) is covered by the
upstream-failure, permission-change, malformed-intent and empty-answer suites.

## Root acceptance addendum

After the worker report, root corrected the database-enforced 200-turn cap,
preserved exact request replay at the cap, and sanitized unexpected error
messages. Root verified the integrated M1 tree with `bash scripts/check-api.sh`:
2,532 API tests passed with zero skips, followed by metric, schema, release
evaluation and whitespace gates. Root npm lint/typecheck/tests and Web/extension
builds passed; disposable PostgreSQL produced 117 runtime tests and 29 schema
assertions. An isolated authenticated Compose smoke exercised create, Today
turn, history, cross-origin rejection and clear. These checks establish local
M1 acceptance only; hosted CI and the full PDF acceptance scenarios remain open.
