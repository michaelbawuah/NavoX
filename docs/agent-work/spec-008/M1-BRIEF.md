# Flash implementation brief — S008-M1

Baseline: stacked `spec-008-navoxbot` branch from `spec-006-news-intelligence` at
`bb0a7c96f4f9a7a6e9acb50fad431f8c546c6256`. Existing untracked raw
SPEC-006/007 validation logs are owner work and must remain untouched. Read
`DESIGN.md` and the PDF-derived R3 constraints in the task message. You are not
alone in the codebase; preserve others' edits and do not revert them.

## Ownership

Own `packages/contracts/src/assistant.ts` and its index export;
`packages/assistant-runtime/**` (new, including explicit PostgreSQL migration
and tests); `apps/web/src/app/api/v1/assistant/**`;
`apps/web/src/app/navox/page.tsx`; assistant-specific components, styles and
client library under `apps/web/src`; the NavoX navigation link in
`apps/web/src/components/navox-ui.tsx`; and the necessary workspace manifests
and `package-lock.json`. You may add focused tests in these paths. Do not edit
existing Python or SPEC-001–007 service logic, `.github/workflows`, deployment,
or owner planning files. Do not commit/push or make paid provider calls.

## Required end-to-end behavior

- Create an AssistantSession through existing authenticated SPEC-005
  `/ai/sessions`, then persist a bounded NavoXbot session row tied to its ID.
  Typed and clicked-microphone turns use the same session ID. The TypeScript API
  supports create, get, message, delete; routes are same-origin and server-side.
- On every call derive user/workspace from current `/auth/me`; never trust client
  IDs for scope. Enforce Origin/JSON on mutations. Bind all reads/writes by
  session+workspace+user. Reject replay of a request ID with changed payload;
  exact retry returns the already saved response. Bound text, turns, retention,
  and concurrent sequence allocation. Do not persist raw audio.
- For `What am I missing today?` or equivalent request, call the existing
  `/today/query` with original text and optional IANA timezone; render its
  `answer`, returned items, details and source IDs as typed structured blocks.
  SPEC-002 owns facts and classification. If it says `unsupported`, return an
  honest `CLARIFY`/unavailable state; do not implement a broad string-matching
  intent router, call model vendors, or invent facts. Failures preserve the turn
  as `UNAVAILABLE` without a fabricated answer or action.
- A `/navox` page has keyboard-accessible typed chat, microphone start/stop,
  visible listening/transcribing/speaking states, text fallback when browser
  speech APIs are unsupported, and a click-to-speak/stop control. Speech and
  typed input share the same session. Typed queries never speak unexpectedly.
  A new user utterance interrupts TTS. Mic use is click-to-talk; there is no
  continuous wake/listening implementation in M1. Define an isolated
  `WakeWordAdapter` interface for later web/native implementations without
  activating it now.
- Keep a provider-neutral, validated TS capability registry and response DTOs
  able to add SPEC-007/005/action routes later. M1 authorizes only read-only
  Today. No action approval endpoint, arbitrary external URL navigation, or
  direct provider SDK.
- Keep `@navox/contracts` type-only; put executable validators in the runtime
  package. The NavoXbot SQL migration is explicitly TypeScript-owned, with a
  testable migration command; do not alter Alembic. Installing `pg` and its
  types from npm is within the authorized build scope, subject to the host's
  sandbox review and the repository security audit.

## Acceptance and verification

Test authenticated scope and cross-workspace denial, missing/bad Origin,
invalid input, concurrent duplicate request IDs, changed replay refusal,
deleted/expired session, Today success/empty/unsupported/upstream failure,
typed silence, clicked voice same session, interruption and unsupported browser
speech fallback. Use fake upstream adapters in unit tests; use disposable local
PostgreSQL for migration and persistence integration if available. Read the
relevant Next.js guide in `node_modules/next/dist/docs/` before Next edits.
Run contracts typecheck and the affected runtime/web lint, typecheck, tests and
build. Report exact commands/results, changed paths, migration status, risks and
remaining SPEC-008 phases. Write a concise report to
`docs/agent-work/spec-008/M1-REPORT.md` only. Stop and report if current exact
head CI becomes red or a required architecture boundary cannot be met without
changing Python/SPEC-005. Do not simulate success by weakening checks.
