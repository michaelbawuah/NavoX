# SPEC-008 M3A — authorized Gmail message draft bridge

Status: approved implementation brief for the existing-service dependency.
This is the minimal Python exception to the owner's primarily TypeScript R3
stack: SPEC-001/003 approval/execution and the existing communication draft
service already live in `services/api`. NavoXbot's public runtime, action
orchestrator, UI and Temporal layer remain TypeScript in the following bundle.

## Problem and bounded outcome

SPEC-007 can resolve an authorized `EMAIL` resource, but the current
`/communication-drafts` service requires a `CommitmentSource` attached to an
owned commitment. Do not create a fake commitment to make a Gmail reply fit.
Extend the existing draft source binding so a single selected, authorized,
owned Gmail message can create a versioned reply draft and then use the same
SPEC-001 exact approval and SPEC-003 verified send workflow. This bundle adds
the service contract and tests only; it does not issue a live send or activate
an AI provider, and it does not yet expose an assistant action route.

## Source and authority contract

- Add a distinct tagged source binding for a `KNOWLEDGE_EMAIL` draft. The
  request accepts only an opaque SPEC-007 `KnowledgeResource.id` for the source
  plus bounded drafting instructions. Never accept displayed provenance,
  connection IDs, external Gmail IDs, thread IDs, sender identity, recipient,
  approval state or permission claims from NavoXbot as authority.
- Only `EMAIL` resources are eligible. An `EMAIL_THREAD` must first resolve to
  one exact authorized message in a later phase; reject a thread-only source.
- Re-resolve server-side under the current authenticated workspace/user. Require
  current `can_view_resource` **and** `owner_user_id == acting user` (VIEW may
  be shared); the canonical source must be live, Google/Gmail, on an active
  connector owned by the user, with a matching active legacy Google Connection
  owned by the same user/workspace and Gmail read scope. The connection ID is
  derived server-side. Recheck this binding on generation, edit, regeneration,
  preparation, approval and execution. A source or permission change blocks
  the action rather than preserving an old grant.
- Persist an explicit binding kind and immutable source selector/external Gmail
  message ID for a knowledge-email draft. Preserve the existing commitment
  binding and behavior. Use a migration to make `commitment_id` nullable only
  for the new tagged kind and enforce the kind/commitment relationship in the
  schema. Keep Alembic at one head and update model/schema consistency.
- The reply recipient must be derived from the freshly fetched Gmail message's
  author under the source mailbox, with a strict email validation. An outgoing
  or ambiguous source with no safe reply recipient is refused. Generate the
  draft only through the existing SPEC-005 `DRAFT_COMMUNICATION` path; no
  provider SDK in NavoXbot and no prompt/output that grants send authority.
  Do not persist the fetched mail body beyond existing authorized data paths.
- Preparation must require `reply_to_source=true`, exact source mailbox,
  provider-fetched reply metadata, source message ID and thread/subject
  consistency. Recheck recipient against the source author for this binding.
  Preserve draft version, payload hash, approval expiry/supersession, pause,
  idempotency and at-most-once behavior. Use `post_send_state="unchanged"` and
  a null commitment only for this validated draft path. The generic
  `/commitments/{id}/actions/gmail-send/prepare` path continues to require a
  real commitment.
- Add a typed endpoint such as `POST /communication-drafts/from-knowledge-email`
  that returns the normal draft detail plus the tagged selector. Existing
  GET/PATCH/regenerate/prepare/approve routes should work through the shared
  source-binding checks, with the new path refusing any unsafe edit before
  approval. Do not build a second approval or send mechanism.

## Verification and stop conditions

Use focused API tests with disposable records and fake Google/AI gateways. Cover
same-owner success, shared-VIEW-but-not-owner refusal, cross-workspace source,
disabled/deleted source, connector/legacy disconnect, stale source identity,
thread-only refusal, unsafe recipient, wrong mailbox, edit-after-approval,
stale approval, and pre-execution revocation blocking without a send. Existing
commitment draft tests must continue to pass. Check migration/model consistency,
Ruff, strict mypy and focused tests; root will run the full API pre-push gate
after integration. No production migration, live Gmail call, live provider call,
mail send, commit or push.

If any of the existing approval/execution invariants cannot be preserved with a
null commitment, stop and report the exact dependency instead of bypassing a
check or creating a synthetic commitment. Do not weaken thresholds or current
security tests.

## Ownership

The Flash writer owns the minimal `services/api` model/migration, communication
draft generation/service/API and approval-source contract changes plus focused
tests and `docs/agent-work/spec-008/M3A-REPORT.md`. It does not edit TypeScript,
M1/M2 reports, checkpoint/design/acceptance matrix, CI/deployment flags,
AGENTS.md, parent SPEC files or unrelated untracked logs. It is not alone in
the repository; preserve root and user edits and accommodate existing changes.
