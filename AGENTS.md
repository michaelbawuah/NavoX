# NavoX development quality gate

## Before changing a branch

Read the current branch and pull-request head, not an earlier conversation's
commit. Preserve unrelated user changes. When CI is red, stop adding features
and diagnose the failed run before continuing the next SPEC milestone.

## Before pushing

Run `bash scripts/check-api.sh` from the repository checkout with the current
`uv.lock`. It runs Ruff lint, Ruff's formatter check, strict mypy, the full API
test suite, Alembic SQL/schema checks, deterministic release evaluation, and
patch whitespace validation. A failed gate must block the push. Do not replace
this with a small selection of tests.

Use `uv run python -m ruff format .` and, where appropriate, Ruff's safe import
fixes from `services/api`; do not guess the formatter output. Fix type errors at
validation boundaries instead of suppressing them. Do not weaken type checks,
release thresholds, security assertions, or CI steps to obtain a green badge.

For web/SDK changes, also run the existing web lint, typecheck, test and build
commands. Check migration/model consistency for persistence changes. Include
regression tests for behavioral fixes. Review the final diff for secrets,
workspace/capability checks, destructive changes, and unrelated edits.

Test the complete proposed working tree, then publish a coherent commit rather
than per-file commits. If code changes after preflight, rerun the gate. If the
exact test environment is unavailable, report that limitation; do not claim an
unexecuted check passed.

## Before declaring a checkpoint complete

Verify hosted CI on the exact new head SHA, including API, Web, Chrome extension,
dependency security, evaluation/hardening, and Compose integration. Queued,
skipped, or still-running checks are not successful checks. Keep draft work
unmerged until its acceptance criteria are met. A green test run does not by
itself establish SPEC completion, live-mail precision, live provider behavior,
or a security certification.
