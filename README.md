# NavoX

NavoX is an AI Operations Platform that helps people understand, prioritize, and safely handle work across their connected tools.

The current implementation includes the **Engineering Foundation through Milestone 8 Chrome Extension**: identity and personal workspaces, authenticated content-minimized events, the Commitment Engine, Today, the bounded agent runtime, exact-action Gmail approval/execution, and deterministic proactive intelligence. NavoX now derives auditable deadline, meeting, renewal, promise, follow-up, and waiting-on-response signals from saved operational state, applies user-controlled quiet hours and fatigue policy, prepares dynamic daily briefings, and uses Temporal for durable lifecycle timers. Google sign-in remains identity-only by default; Gmail send authority is still separate and approval-bound. Milestone 7 adds no Gmail-read, Calendar-read, or Drive-read authority and performs no proactive provider writes.

## Repository layout

```text
apps/web/              Next.js web client
services/api/          FastAPI API, SQLAlchemy models, Alembic migrations, tests
workflows/temporal/    Temporal worker entry point
packages/contracts/    Shared TypeScript API contracts
packages/ui/           Reserved shared UI package
docs/architecture/     Authoritative product and system specification
docs/adr/              Accepted architecture decisions
.github/workflows/     CI
```

## Prerequisites

- Node.js 20.9 or later (the repository currently uses Node 24)
- npm 11 or later
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker Engine with Docker Compose v2 for local PostgreSQL and Temporal

## First-time setup

```bash
git clone https://github.com/michaelbawuah/NavoX.git
cd NavoX
cp .env.example .env
npm ci
(cd services/api && uv sync --all-groups)
docker compose up -d postgres temporal temporal-ui
(cd services/api && uv run alembic upgrade head)
```

Start the API in one terminal:

```bash
cd services/api
uv run fastapi dev navox/api/main.py
```

Start the web client in another:

```bash
npm run dev --workspace=@navox/web
```

Open the web shell at `http://localhost:3000`, API documentation at `http://localhost:8000/docs`, and the Temporal UI at `http://localhost:8080`.

To run the Temporal worker after services are ready:

```bash
cd services/api
uv run python -m navox.workflows.worker
```

The worker hosts the foundation workflow, Milestone 5's `HandleCommitmentWorkflow`, Milestone 6's durable `ApprovedActionWorkflow`, and Milestone 7's commitment-lifecycle, follow-up, meeting-preparation, and daily-briefing workflows. Docker Compose starts the same worker automatically.

To run all checks that do not require local Docker services:

```bash
npm run lint && npm run typecheck && npm run test && npm run build
(cd services/api && uv run ruff check . && uv run ruff format --check . && uv run mypy navox && uv run pytest)
(cd services/api && uv run alembic upgrade head --sql > /tmp/navox-schema.sql)
```

## Environment and secrets

Copy `.env.example` to `.env`; it contains local-only defaults and empty placeholders. Never commit `.env` or real credentials. Passwords are Argon2-hashed; browser sessions use HttpOnly, SameSite cookies backed by hashed server-side tokens. Google OAuth refresh tokens are encrypted using `GOOGLE_TOKEN_ENCRYPTION_KEY` and are referenced from connection records rather than stored in plaintext application columns.

To enable the optional Google connection locally, create a Google Cloud OAuth **Web application** client and set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_TOKEN_ENCRYPTION_KEY` in `.env`. Register this exact authorized redirect URI:

```text
http://localhost:8000/api/v1/connections/google/callback
```

Generate the encryption key without printing any other secret material:

```bash
(cd services/api && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

Milestone 2's event endpoints are intentionally not configured by local defaults. Calendar and Drive notifications must use a server-created notification channel with an opaque channel token; Gmail needs an authenticated Pub/Sub push subscription. Set the four `GOOGLE_*PUSH*` event-delivery values only in protected deployment configuration after a public HTTPS endpoint exists. Do not put those values in source control or expose them to the browser.

## Authentication endpoints

- `POST /api/v1/auth/register` creates an account, one personal workspace, owner membership, and session.
- `POST /api/v1/auth/login` creates a new server-side session.
- `GET /api/v1/auth/me` returns the authenticated account and accessible personal workspace.
- `POST /api/v1/auth/logout` revokes the current server-side session.

## Google connection endpoints

- `GET /api/v1/connections/google/start` creates a state- and PKCE-protected Google authorization request for the current personal workspace.
- `GET /api/v1/connections/google/callback` finishes the provider callback and redirects to the web client; it never returns tokens to the browser.
- `GET /api/v1/connections/google` lists only the current user's workspace-scoped Google connections.
- `POST /api/v1/connections/google/{connection_id}/health` validates a stored connection with Google’s token endpoint only; it does not access any Google content API.
- `GET /api/v1/connections/google/{connection_id}/gmail-send/start` begins a separate incremental OAuth grant for only `gmail.send`. Identity sign-in never silently adds email authority.

## Provider event endpoints

These public machine-to-machine endpoints never receive a user or workspace ID from the caller. NavoX resolves ownership only from a stored connection or server-created notification channel, stores normalized references and hashes rather than raw provider content, and treats repeated delivery IDs as idempotent.

- `POST /api/v1/events/calendar` accepts a Google Calendar notification only when its channel ID, resource ID, and server-issued channel token match an active stored subscription.
- `POST /api/v1/events/drive` uses the same server-created-channel verification for Drive notifications.
- `POST /api/v1/events/gmail` requires both a configured Pub/Sub verification token and a signed Google Pub/Sub OIDC JWT, then records the Gmail history-change reference against exactly one active Google connection.

The watch-creation and renewal worker that creates Calendar/Drive channels and Gmail mailbox watches is intentionally deferred until users explicitly opt into the appropriate Google content scopes. The event pipeline is ready to receive those authenticated notifications, but does not fetch or retain mail, calendar, or Drive content yet.

## Commitment Engine

Milestone 3 provides a provider-neutral internal boundary for structured AI extraction. Untrusted model output is accepted only as a strict JSON shape, rejects undeclared fields and instruction-like content, requires timezone-aware due dates, and cannot specify tools or external actions. The deterministic policy then applies these local thresholds:

- Confidence at or above `COMMITMENT_HIGH_CONFIDENCE_THRESHOLD` (default `0.85`) creates a `confirmed` commitment.
- Confidence at or above `COMMITMENT_MODERATE_CONFIDENCE_THRESHOLD` (default `0.65`) and below the high threshold creates a `candidate` for review.
- Lower-confidence candidates are suppressed.

Created commitments retain only normalized facts and source references. Provenance links each commitment to its incoming event; raw provider content is not copied into NavoX. The engine also stores safe `depends_on`, `blocks`, and `related_to` relation edges. Evaluation fixtures in `evals/commitments/` cover instruction-like output, undeclared execution fields, malformed relations, and false-positive candidates.

Authenticated review endpoints are workspace- and user-scoped:

- `GET /api/v1/commitments` lists the current user's commitments; pass `?status=candidate` to focus review.
- `GET /api/v1/commitments/{commitment_id}` returns its normalized facts, provenance references, and outgoing relation edges.
- `POST /api/v1/commitments/{commitment_id}/confirm` confirms a moderate-confidence candidate.
- `POST /api/v1/commitments/{commitment_id}/reject` rejects a moderate-confidence candidate.

There is intentionally no client-facing endpoint to submit extraction output, and these endpoints never trigger an external action.

## Local containers

`docker compose up --build` starts PostgreSQL, Temporal, Temporal UI, a one-time database migration service, API, and web services. `docker compose down` stops them; append `-v` only when intentionally discarding local database data.

## License

The repository is private and no open-source license has been selected yet. No permission is granted for external use until the owner makes that legal decision.


## Bounded agent endpoints

- `POST /api/v1/commitments/{id}/handle` creates an idempotent, persisted plan and dispatches its safe steps to Temporal.
- `GET /api/v1/plans` lists recent plans in the authenticated workspace.
- `GET /api/v1/plans/{id}` returns persisted step, action, risk, result, and workflow progress.
- `GET /api/v1/agent/state` returns whether agent execution is paused.
- `POST /api/v1/agent/pause` prevents new plans and causes future steps to fail closed at policy evaluation.
- `POST /api/v1/agent/resume` re-enables bounded execution.

Milestone 5 has a hard eight-step ceiling and two-replan design limit. The current deterministic planner uses three internal actions: `navox.commitment.inspect` (R0), `navox.context.prepare` (R1), and `navox.next_steps.prepare` (R1). Gmail, Calendar, and Drive contracts are registered with fixed risk and permission requirements so future planners cannot invent or downgrade risk, but provider executors remain disabled until the matching scope and milestone are deliberately implemented. R2+ actions are never executed by the Milestone 5 worker.


## Approval and verified execution

Milestone 6 adds the first consequential external action: **Gmail send (R3)**. NavoX prepares the exact email first and persists the canonical security-relevant payload, its hash, an expiring versioned approval, the plan step, and the action record. The user can revise, approve, or reject that exact action.

- `POST /api/v1/commitments/{id}/actions/gmail-send/prepare` prepares an R3 send without contacting Gmail.
- `GET /api/v1/actions` and `GET /api/v1/actions/{id}` expose tenant-scoped persisted action/approval status.
- `POST /api/v1/actions/{id}/edit` supersedes the previous approval and creates a new payload hash/version.
- `POST /api/v1/actions/{id}/approve` authorizes only the currently hashed payload.
- `POST /api/v1/actions/{id}/reject` terminates the prepared action without provider execution.

Security and reliability invariants:

- approvals expire after 15 minutes;
- material edits invalidate the previous approval;
- pause invalidates an approved-but-not-yet-executed action and requires fresh approval;
- approval consumption is serialized with database row locks;
- Gmail permission and sender/connection ownership are revalidated at execution time;
- Gmail `messages.send` is treated as at-most-once because the provider does not expose an idempotency key;
- after the provider request begins, ambiguous transport failures enter `manual_review` and are never blindly retried;
- a returned Gmail message ID is the independent verification signal stored in the action result and audit event.

See `docs/architecture/milestone-6-approval-execution.md` for the full boundary.


## Proactive NavoX

Milestone 7 turns persisted operational state into proactive, explainable attention signals. The engine uses deterministic score components—urgency, consequence, user priority, objective relevance, actionability, waiting duration, interruption cost, and notification fatigue—and stores the full breakdown alongside every signal.

The default attention tiers are evaluation policy, not hidden model behavior:

- `notify_now`: score 85–100, subject to quiet hours, pause state, cooldown, and daily interruption budget;
- `briefing`: score 65–84, or a higher score whose interruption is suppressed by fatigue policy;
- `dashboard`: score 40–64;
- `suppressed`: below 40, snoozed, dismissed, or otherwise not appropriate to surface.

Every visible signal answers what is happening, why it matters, and which bounded NavoX capability can help. Opening a briefing manually does not count as an interruption. Only actual notify-now surfacing contributes to notification fatigue.

Authenticated proactive endpoints:

- `GET /api/v1/proactive/preferences` reads quiet hours, thresholds, briefing hour, cooldown, and interruption budget.
- `POST /api/v1/proactive/preferences` updates those user-controlled policies and the workspace timezone.
- `POST /api/v1/proactive/evaluate` deterministically refreshes proactive state.
- `GET /api/v1/proactive/briefing` recomputes a fresh briefing from current state and records only an audit snapshot.
- `GET /api/v1/proactive/meeting-prep` prepares the next saved meeting from NavoX state and related commitments.
- `POST /api/v1/proactive/signals/{id}/snooze` suppresses a signal until an explicit future timestamp.
- `POST /api/v1/proactive/signals/{id}/dismiss` dismisses the current material version of a signal.
- `POST /api/v1/proactive/activate` idempotently starts durable Temporal scheduling for the workspace and eligible commitments.

Today chat now also supports “What am I forgetting?”, “Prepare me for my next meeting”, “Anything costing me money soon?”, and “What can you handle for me?” using the same saved state rather than a separate chat memory.

The current `notify_now` label is an **in-product attention tier**. Milestone 7 does not claim OS push, SMS, email-notification, or mobile background delivery. Scheduled workflows keep state and briefing readiness durable; a future delivery channel must be added explicitly and permissioned separately.

See `docs/architecture/milestone-7-proactive-navox.md` for scoring, fatigue, persistence, workflow, and safety invariants.


## Chrome extension

Milestone 8 adds a Chrome Manifest V3 side panel under `apps/extension/`. It is
a thin client of the same NavoX API and does not contain a second agent runtime.

The side panel exposes:

- Today and proactive briefing state;
- operational queries such as “What am I forgetting?”;
- Handle this for saved commitments;
- recent bounded-plan progress;
- exact persisted R3 approval review and explicit approve/reject gestures;
- snooze and dismiss controls for proactive signals.

The extension deliberately requests only `sidePanel`, `storage`, and one
explicit NavoX API host permission. It has no content scripts and requests no
`tabs`, `activeTab`, `scripting`, `cookies`, or `webRequest`
permission. It does not read the page the user is viewing.

Build the local extension:

```bash
npm ci
npm run build --workspace=@navox/extension
```

Then open `chrome://extensions`, enable **Developer mode**, choose
**Load unpacked**, and select:

```text
apps/extension/dist
```

The local build talks to `http://localhost:8000` and links to the web
workspace at `http://localhost:3000`. For a deployed environment, build with
one explicit HTTPS API and web origin:

```bash
NAVOX_EXTENSION_API_ORIGIN=https://your-api.example \
NAVOX_EXTENSION_WEB_ORIGIN=https://your-web.example \
npm run build --workspace=@navox/extension
```

The build rejects insecure non-local HTTP origins, forbidden browser
permissions, content scripts, and remote/dynamic executable code.

Extension authentication uses a separate opaque bearer session. The password is
sent only during login and is never stored by the extension. Chrome stores the
opaque session in extension-local storage; PostgreSQL stores only its hash.
Extension logout revokes that extension session without revoking the user's web
session.

See `docs/architecture/milestone-8-chrome-extension.md` for the browser
permission, authentication, and safety boundary.
