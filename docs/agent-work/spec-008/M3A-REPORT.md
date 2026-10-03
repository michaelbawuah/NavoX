# SPEC-008 M3A service bridge — local acceptance

Status: locally accepted on `spec-008-navoxbot`, uncommitted and unpushed. The
Flash builder's route returned an insufficient-balance error after writing a
partial patch. Astra completed formatting, security review, corrections and
verification directly. No provider was activated, no paid model or live Gmail
call was made, and no production migration or email send occurred.

The existing communication-draft service now accepts one opaque SPEC-007
`EMAIL` resource ID at `POST /communication-drafts/from-knowledge-email`. It
re-resolves current VIEW authority and enforces **mailbox ownership** as well
as the connector-to-legacy Google account binding. It rejects thread-only and
shared-only sources. Gmail author, recipient and reply metadata come from a
fresh server-side message fetch; SPEC-005 remains the only draft-generation
model path. A tagged, versioned draft pins the source message and may then use
the existing edit, prepare, exact approval and verified-send endpoints. The
generic commitment action endpoint still requires a commitment. Every
consequential boundary rechecks the source, account, version and payload hash.

Migration `0034_knowledge_email_drafts` adds the explicit binding kind,
source message ID and a nullable commitment only for the new kind. Its
downgrade refuses to proceed while new drafts exist, avoiding silent data
deletion. The migration was applied successfully to disposable PostgreSQL;
targeted migration/model tests found no drift on `communication_drafts`.
Broad `alembic check` remains red because of existing repository-wide drift
and the separate TypeScript-owned assistant tables, with no reported
`communication_drafts` discrepancy.

Verification: 63 focused draft/approval/migration tests passed; Ruff lint,
Ruff format and strict mypy passed. The exact `bash scripts/check-api.sh`
preflight then passed with 2,576 API tests, zero skips, all metric thresholds,
Alembic SQL/schema, deterministic release evaluation and patch whitespace.
Log: `/tmp/navox-spec008-m3a-api-gate-20260930.log`. Synthetic tests cover
draft→prepare→approve→verified fake send, shared VIEW without mailbox ownership,
revoked/deleted/changed sources, wrong connection and recipient, edit after
approval, expired/stale approval, and zero-send execution blocks.

This is a service bridge, not an accepted assistant email workflow. The
TypeScript NavoXbot action orchestrator and UI must now resolve an exact
message, present the draft and approval hash/version, and verify the action
state. Hosted CI and real-provider/human acceptance remain open.
