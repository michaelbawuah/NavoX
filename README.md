# NavoX

NavoX is an AI Operations Platform that helps people understand, prioritize, and safely handle work across their connected tools.

The current implementation includes the **Engineering Foundation** plus the first bounded Milestone 1 slices: email/password authentication, opaque server-side sessions, automatic creation of one personal workspace, and an opt-in Google identity connection. The Google flow requests only OpenID profile and verified-email scopes; no Gmail, Calendar, or Drive data is requested or ingested. AI providers and external actions remain deliberately absent until their dedicated milestones described in [SPEC-001](docs/architecture/SPEC-001-navox.md).

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

To run the Temporal foundation worker after services are ready:

```bash
cd services/api
uv run python ../../workflows/temporal/worker.py
```

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

## Local containers

`docker compose up --build` starts PostgreSQL, Temporal, Temporal UI, a one-time database migration service, API, and web services. `docker compose down` stops them; append `-v` only when intentionally discarding local database data.

## License

The repository is private and no open-source license has been selected yet. No permission is granted for external use until the owner makes that legal decision.
