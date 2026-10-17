# Local development

The quickest way to run NavoX is the [Docker setup](../README.md#run-locally).
This guide runs the application processes on the host, with PostgreSQL and
Temporal in containers.

## First-time setup

Install Node 24, npm 11, Python 3.12, `uv`, and Docker with Compose v2.
From the repository root:

```bash
cp .env.example .env
npm ci
(cd services/api && uv sync --locked --all-groups)
docker compose up -d postgres temporal temporal-ui
(cd services/api && uv run --env-file ../../.env alembic upgrade head)
uv run --no-project --env-file .env npm run migrate --workspace=@navox/assistant-runtime
```

The example environment uses local database credentials and disables AI provider
traffic. Put deployment credentials in untracked environment files.

## Start the application

Run each process in its own terminal. Start these from the repository root; the
commands load the root `.env` explicitly.

| Process | Command |
| --- | --- |
| Python API | `cd services/api && uv run --env-file ../../.env fastapi dev navox/api/main.py` |
| Web client | `uv run --no-project --env-file .env npm run dev --workspace=@navox/web` |
| Foundation worker | `cd services/api && uv run --env-file ../../.env python -m navox.workflows.worker` |
| Assistant goals worker | `uv run --no-project --env-file .env npm run worker:goals --workspace=@navox/assistant-runtime` |

Open the [web app](http://localhost:3000), [API docs](http://localhost:8000/docs),
and [Temporal UI](http://localhost:8080). Register a local account. Connected
accounts and model-backed features require their own configuration.

The foundation worker processes connector events, commitments, approved actions,
and source lifecycles. The goals worker monitors saved assistant outcomes on a
separate queue. Start one instance of each for a normal local session.

## Database ownership

Python migrations live in [services/api/migrations](../services/api/migrations).
Assistant runtime migrations live in
[packages/assistant-runtime/migrations](../packages/assistant-runtime/migrations).
Apply both before starting the application after a schema change. Compose runs
these migration services automatically.

Python uses a `postgresql+asyncpg://` URL. The Node runtime uses a `postgres://`
URL through `NAVOX_ASSISTANT_DATABASE_URL`; the Python driver suffix does not
belong in that value.

## Connected features

- [Connector operations](architecture/spec-002-operations.md): OAuth callbacks,
  explicit content permissions, event delivery, and sync recovery.
- [Gateway configuration](architecture/spec-005-gateway.md): provider keys,
  versioned task bindings, qualification, and routing policy.
- [News sources](../config/news/README.md): source templates, permissions,
  retention, and account-specific activation.
- [Chrome extension](architecture/milestone-8-chrome-extension.md): build, load,
  and configure the side panel.

Provider keys and OAuth tokens remain server-side. Account identity, permission
to read a source, and permission to execute an action are separate checks.

## Checks

```bash
npm run lint
npm run typecheck
npm run test
npm run build
bash scripts/check-api.sh
```

The complete infrastructure-dependent suites require disposable PostgreSQL and
Temporal services. See the [quality guide](development/api-quality.md) and
[CI workflow](../.github/workflows/ci.yml) for the full gates.

Use the [production deployment guide](../deploy/README.md) for the VPS overlay,
HTTPS routing, existing-data preservation, and rollback.
