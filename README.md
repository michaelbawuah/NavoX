# NavoX

NavoX is an AI Operations Platform that helps people understand, prioritize, and safely handle work across their connected tools.

The current implementation is **Milestone 0: Engineering Foundation**. It establishes a Next.js web shell, FastAPI API shell, PostgreSQL tenancy migration, Temporal worker boundary, local Docker services, reproducible dependency locks, health checks, and CI. It intentionally contains no user authentication, Google OAuth, raw-content ingestion, AI provider, or external-action implementation. Those begin only in later milestones described in [SPEC-001](docs/architecture/SPEC-001-navox.md).

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

Copy `.env.example` to `.env`; it contains local-only defaults and empty placeholders. Never commit `.env` or real credentials. Phase 1 OAuth refresh tokens will be referenced through protected secret storage rather than stored as plaintext application columns.

## Local containers

`docker compose up --build` starts PostgreSQL, Temporal, Temporal UI, a one-time database migration service, API, and web services. `docker compose down` stops them; append `-v` only when intentionally discarding local database data.

## License

The repository is private and no open-source license has been selected yet. No permission is granted for external use until the owner makes that legal decision.
