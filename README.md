# NavoX

NavoX is an AI Operations Platform that helps people understand, prioritize, and safely handle work across their connected tools.

The current implementation includes the **Engineering Foundation** through **Milestone 5's Bounded Agent**: identity and personal workspaces, authenticated content-minimized events, the Commitment Engine, the Today operational workspace, bounded read-only Today queries, and a durable Handle This runtime backed by PostgreSQL and Temporal. Plans, ordered steps, action records, workflow references, and audit events are persisted; action risk is owned by code-level contracts, plan creation is idempotent, and Milestone 5 automatically executes only internal R0/R1 reads and preparation. The Google flow still requests only OpenID profile and verified-email scopes; no Gmail, Calendar, or Drive content is read, and no external provider action executes in this milestone.

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

The worker hosts the foundation workflow and Milestone 5's `HandleCommitmentWorkflow`. Docker Compose starts the same worker automatically.

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
