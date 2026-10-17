# Assistant runtime

The assistant coordinates saved conversations and existing NavoX capabilities.
The TypeScript runtime owns its interaction state; the Python API owns accounts,
connected data, provider policy, drafts, and action execution.

## Service boundaries

- The [Next.js routes](../../apps/web/src/app/api/v1/assistant) expose session,
  message, draft, speech, navigation, and goal endpoints. Mutations enforce the
  configured origin and expected content type through the
  [route guard](../../apps/web/src/lib/assistant-route.ts).
- The [runtime](../../packages/assistant-runtime/src/runtime.ts) authenticates
  upstream, derives the user/workspace scope, validates plans and responses,
  and delegates capabilities through the
  [API adapter](../../packages/assistant-runtime/src/gateway.ts).
- [Persistence](../../packages/assistant-runtime/src/store.ts) stores runtime
  sessions, ordered turns, request claims, and goals in PostgreSQL. Runtime
  sessions reference the API's `assistant_sessions` identity; the schemas have
  separate [TypeScript migrations](../../packages/assistant-runtime/migrations)
  and [Alembic migrations](../../services/api/migrations).
- Repeated request IDs replay the recorded result. Reusing an ID with different
  content is rejected. A durable claim precedes upstream work to prevent
  concurrent retries from starting the same work twice.

## Planning, conversation, and actions

The [intent planner](../../services/api/navox/ai/intent_plan.py) returns a bounded
plan. The [plan validator](../../packages/assistant-runtime/src/planner.ts)
checks copied utterance spans, supported capabilities, and session-owned
references before dispatch. A plan can contain at most four intents.

Ordinary conversation uses a separate
[Python service](../../services/api/navox/ai/conversation.py). Its
[runtime context selector](../../packages/assistant-runtime/src/conversation.ts)
includes only verified ordinary answers from the same saved session; the API
independently verifies their exact stored text. Connected-source results,
evidence cards, and drafts do not become ordinary conversation history.

Connected reads, such as Today, meetings, subscriptions, and news, remain with
their owning services. Email preparation, editing, approval, and action reads
use the [email delegation layer](../../packages/assistant-runtime/src/email-actions.ts).
Generated prose and planner output confer no action authority. Consequential
execution stays behind the owning API's exact approval and action ledger.
Provider eligibility and configuration are described in the
[gateway policy](spec-005-gateway.md).

## Bounds and deadlines

The canonical runtime constants live in
[`limits.ts`](../../packages/assistant-runtime/src/limits.ts).

| Boundary | Default |
| --- | --- |
| Current question | 500 characters |
| Planner context | Last four eligible questions, each at most 240 characters |
| Ordinary conversation context | Last four verified pairs; question 500 / answer 3,000 characters |
| Runtime answer / ordinary conversation answer | 4,000 / 3,000 characters |
| Retained runtime turns | 200 per session |
| Session retention | 30 days; bounded scheduled cleanup |
| Ordinary API requests | 10 seconds |
| AI planning or conversation request | 35 seconds |
| Source-backed draft generation | 65 seconds |
| Duplicate wait / stale request claim | 5 seconds / 360 seconds |
| Speech proxy requests | 30 seconds |

Long questions remain valid turns but are excluded from the shorter planner
reference list. Ordinary conversation history applies its independent bounds.
The API adapter accepts timeout overrides only within the supported ceilings.

## Durable goal verification

The [goal service](../../packages/assistant-runtime/src/goal-service.ts) records
briefing, meeting-preparation, and communication-action goals. The
[Temporal workflow](../../packages/assistant-runtime/src/temporal/workflows.ts)
receives exactly `{ goal_id }`, without cookies, question text, or credentials.
It polls at five-minute intervals, at most 12 times, within a one-hour window.
Dispatch attempts are limited to three.

Each [activity](../../packages/assistant-runtime/src/temporal/activities.ts)
re-reads the goal and verifies that its saved turn or action still belongs to
the stored user and workspace. A communication action becomes `COMPLETED` only
when the owning ledger records completion and verification. Pending approval
stays `WAITING_FOR_USER`; an executed but unverified action stays
`WAITING_FOR_EXTERNAL`. The worker observes recorded state and does not approve,
execute, retry, or cancel the underlying action. See the
[state rules](../../packages/assistant-runtime/src/goals.ts).

## Voice and local wake controls

The [browser speech adapter](../../apps/web/src/lib/assistant-speech.ts) captures
one in-memory, mono 16 kHz WAV clip, limited to 30 seconds and 1,000,000 bytes.
The session-scoped route validates it before forwarding it to the API speech
gateway. The transcript enters the same message path as typed input.

Spoken replies select a saved turn or owned draft; the server derives the text,
limited to 600 characters. The synthesis proxy returns bounded MP3 audio, at most
2 MiB. Text remains available when capture or playback fails. Hands-Free is an
explicit opt-in. [Wake detection](../../apps/web/src/lib/assistant-wake.ts)
requires on-device browser recognition and stops when disabled or the page is
hidden. Local voice activity can interrupt playback and begin the next bounded
recording. [Voice state rules](../../packages/assistant-runtime/src/voice.ts)
and the [controller](../../apps/web/src/lib/assistant-controller.ts) coordinate
these transitions.

## Configuration and verification

The [server factory](../../apps/web/src/lib/assistant-server.ts) reads
`NAVOX_API_BASE_URL`, `NAVOX_ASSISTANT_DATABASE_URL` (or `DATABASE_URL`),
`NAVOX_ASSISTANT_ORIGIN`, and optional `NAVOX_ASSISTANT_PURGE_INTERVAL_MS`.
The [Temporal configuration](../../packages/assistant-runtime/src/temporal/config.ts)
uses `NAVOX_ASSISTANT_TEMPORAL_TARGET`, `NAVOX_ASSISTANT_TEMPORAL_NAMESPACE`, and
`NAVOX_ASSISTANT_TEMPORAL_TASK_QUEUE`; the default queue is `navox-assistant-goals`.
[Compose](../../docker-compose.yml) runs assistant migrations before the web
service and the separate goal worker.

Runtime tests sit beside their modules, including PostgreSQL store/goal tests.
Run `npm run test --workspace=@navox/assistant-runtime` and
`npm run migration:check --workspace=@navox/assistant-runtime`.
Browser adapter, controller, and route tests are in
[`apps/web/src/lib`](../../apps/web/src/lib); provider conversation, planning,
and speech tests are in [`services/api/tests`](../../services/api/tests).
Infrastructure-dependent checks require disposable PostgreSQL/Temporal services;
fixture tests alone do not establish live connector or speech behavior.
