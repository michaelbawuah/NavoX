# SPEC-008 M3B — email action orchestration checkpoint

Status: local TypeScript slice implemented on `spec-008-navoxbot`; uncommitted,
unpushed, and not yet accepted as full SPEC-008.

The NavoXbot runtime now permits a reply draft only from one complete,
session-owned SPEC-007 `EMAIL` result. It binds the opaque resource ID to the
existing communication-draft service, validates the returned source and draft
version, and exposes create, read, revise, prepare, exact approve, and action
status methods through authenticated same-origin Next API routes. The service
still owns Gmail identity, fresh source checks, AI generation through SPEC-005,
SPEC-001 approval, and SPEC-003 send/verification. NavoXbot does not send mail
directly.

The assistant UI renders an explicit draft editor and exact send review. The
operator must check a confirmation control and click approval; a typed or voice
transcript cannot approve. The UI reports success only when the existing action
ledger returns `completed` with a verified message ID. A manual status refresh
handles pending execution. A browser tab retains only the session and draft ID
pointers so it can reload the server-owned draft and action review without
regenerating a paid draft. Runtime tests cover an alien source turn and hash
mismatch before approval; route tests cover cross-origin and missing-cookie
mutations. Synthetic tests have not sent live mail.

Local checks on this slice: runtime 163 tests passed against disposable
PostgreSQL, zero skipped, and its live migration check passed 29 assertions.
Web 277 tests, lint, typecheck and production build passed; root workspace
typecheck/test and Chrome extension build also passed. The complete
`bash scripts/check-api.sh` gate passed with disposable PostgreSQL and Temporal:
2,576 API tests, zero skips, and all Ruff, mypy, SPEC-003/004 metric, Alembic
SQL/schema, deterministic evaluation and whitespace checks. Log:
`/tmp/navox-spec008-m3b-api-gate-pg-20260930.log`.

Open for acceptance: real provider/catalog configuration and authorized Gmail
end-to-end observation, ambiguous thread/person clarification across turns,
cross-device draft discovery, richer action activity events, voice read
aloud of draft, and the remaining SPEC-008 domains and workflows. No live AI
provider or Gmail calls were made here.
