# Development quality checks

## Branch and release review

Read the current branch and pull-request head before making changes. Preserve
unrelated work. Diagnose failing CI before continuing a feature milestone.

Review the final diff for credentials, workspace/capability checks, destructive
changes, and unrelated edits. Do not weaken type checks, release thresholds,
security assertions, or CI steps to obtain a passing run.

## API preflight

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

## Publication requirements

Run the complete API preflight on the final tree using the committed `uv.lock`.
A failed gate blocks publication. Fix type errors at validation boundaries
rather than suppressing them. Include regression tests for behavioral fixes.
For persistence changes, also check migration/model consistency.

For web/SDK changes, run the existing web lint, typecheck, test, and build
commands. Use the exact tree that passed preflight for the commit. If code
changes after validation, rerun the complete API preflight and applicable
web/SDK gates. Publish a coherent commit rather than a series of per-file
commits. Report unavailable test infrastructure and skipped checks explicitly.

Before declaring a checkpoint complete, verify hosted CI on the new head SHA,
including API, Web, Chrome extension, dependency security, evaluation/hardening,
and Compose integration. Queued, skipped, and running checks are not successful
checks. Keep draft work unmerged until its acceptance criteria are met.

A passing test run does not establish full SPEC completion, live-mail precision,
live-provider behavior, or a security certification. Report those outcomes
separately with their supporting evidence.
