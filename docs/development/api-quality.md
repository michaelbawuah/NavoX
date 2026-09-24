# API preflight

From the repository root, before pushing a change:

```bash
bash scripts/check-api.sh
```

The preflight uses the committed lockfile. After dependency installation, it
runs every quality check even when an earlier check fails, then exits nonzero
if any check failed. This exposes lint, formatting, type, test, migration, and
evaluation problems together instead of requiring a new CI run for each layer.
It does not alter the application, auto-format files, or weaken hosted CI.

To run the same gate automatically before local pushes, opt in for this clone:

```bash
git config core.hooksPath .githooks
```

The hook is local and is not activated merely by pulling the repository. Inspect
an existing `core.hooksPath` configuration before changing it. This hook checks
the current working tree; review staged/unstaged changes and push the same tree
that passed. Do not bypass a failed gate with `--no-verify`.

To fix formatting/import errors before rerunning the preflight:

```bash
cd services/api
uv run python -m ruff check --fix .
uv run python -m ruff format .
```

Review auto-fixes, then run the full preflight again from the root. Web/SDK
quality, dependency-security scans and the hosted PostgreSQL/Temporal Compose
integration remain additional CI checks. Mock-provider tests are not proof of
live-provider acceptance.
