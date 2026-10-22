# Content-free goal verification preparation

This additive source increment records the existing service's bounded grounding
classification separately from a turn's private content. It does not encrypt
history, change retention, create derived memory, backfill legacy evidence,
switch a worker, or enable a capability.

## Authority and compatibility

| Boundary | Behavior in this increment |
| --- | --- |
| Existing worker | `verifyGoal`, `markGoalDeadline`, workflow/activity names, goal-ID-only payloads, poll budgets and the existing plaintext `readGoalSourceCheck` remain unchanged. |
| New turn writer | One PostgreSQL data-modifying CTE saves the turn and its content-free projection atomically; a projection failure rolls back the turn. |
| New read seam | `readGoalVerificationProjection` selects owned identifiers, source state, the closed projection and existing action status/timestamps. It never selects question, answer, plan, decision, presentation or action payloads. |
| Unavailable evidence | Legacy, invalidated or malformed evidence returns a null projection. The helper reports `available:false, outcome:null`; absence cannot prove either success or failure. No plaintext fallback or inference from goal status fills the gap. |
| Captured negative evidence | A valid stored `UNREADABLE` or `NOT_GROUNDED` attestation remains available evidence with the existing bounded negative outcome. It is distinct from an absent attestation. |
| Consequential actions | The existing live action ledger remains authoritative. An uncertain Gmail action is not retried, reset, deleted, approved or otherwise changed by this preparation. |

`GoalProjectionStore` is a separate additive interface. The existing
`AssistantStore` contract and its deployed worker callers do not change. The new
pure helper is available for a later qualified cutover; it is not wired into the
running goal activity entrypoint here. The caller must establish source ownership
before using it, and the helper alone cannot authorize a goal-state write.

The pinned Node worker source is
`62981c2b7f561aa8c3612a69f702ce32086d9dac`; its image digest is
`sha256:609171641f600a8199e4eb8acd16fd26effb9392bb223718cadc0a0b38d77ac7`.
That compiled worker still requires untouched plaintext provenance columns.
Additional schema objects do not remove this dependency. Do not encrypt,
redact or drop those columns while that worker remains in service.

## Projection contract

TypeScript-owned migration `0003_goal_turn_projections.sql` adds
`assistant_runtime_turn_goal_projections` with only:

- Exact turn/session/workspace/user UUIDs.
- `contract_version = 1`.
- One classification: `BRIEFING`, `MEETING_PREP`, `NOT_GROUNDED` or `UNREADABLE`.
- The source turn's creation timestamp.

A composite foreign key binds all four identifiers to the saved turn; a matching
unique index on that turn supports the binding. The reader joins the exact source
scope before exposing evidence. A foreign source remains unowned even if its
opaque identifier exists.

The writer uses the existing strict plan, decision and presentation parsers and
`groundedTodayTurn`. A meeting classification still requires a briefing linked
to an item carried by that same turn. No question, answer, email body, source
snippet, title, arbitrary provider reason, credential, content hash or embedding
is copied into the projection. This attests the existing structural service
contract; it does not introduce an accuracy claim or action permission.

The pure parser accepts only the exact versioned two-field contract. Unknown
versions, extra fields and malformed values are unavailable evidence. Valid
captured unreadable evidence retains `goal.turn_unreadable`; a valid readable
ungrounded/mismatched result retains `goal.turn_not_grounded`. A non-ready current
source cannot expose usable success evidence even if an inconsistent projection
row is supplied.

## Invalidation and erasure

A database trigger deletes a projection before a semantic or ownership update
changes any of `id`, `session_id`, `workspace_id`, `user_id`, `state`, `plan`,
`decision` or `presentation`. It never silently re-derives or upgrades evidence.
The deletion and source update share the same transaction, including rollback.
No-op updates preserve evidence. Question/answer-only changes do not invalidate
it because those fields are not verification inputs.

Deleting the original turn or owning runtime session removes the projection
through the foreign key. The original turn-to-goal cascade remains in place.
This adds no independently retained history or longer expiry: the runtime
session's thirty-day maximum still applies. Shared memory deletion, encrypted
history and the requested thirty-/ninety-day/never choices remain separate work.

## Migration and future cutover gates

The migration adds an index, one table, an invalidation function and trigger. Its
DDL is transactional and serialized by a migration-specific advisory lock. It
never updates legacy source content, goal status, action rows, retention or the
Alembic head. Rerunning it preserves evidence and does not populate missing rows.

The new turn writer requires migration 0003 before that new application source
is used. The old worker's migration command only knows its old files and cannot
satisfy this precondition. A separately qualified release must apply and inspect
the exact new schema before deploying the new writer. No production operation is
part of this increment.

Before a future worker or encryption cutover:

1. Qualify an explicit bounded legacy migration using the same strict parsers,
   exact ownership and concurrent update/deletion fences. Missing evidence stays
   unknown; never derive it from `COMPLETED` or goal kind.
2. Couple proof verification to completion atomically. The current read object
   is a snapshot, not completion authority: a semantic update may invalidate it
   immediately after SELECT. A future worker must lock/recheck the exact goal,
   source turn and projection bindings/version/classification/state and apply
   completion in that same transaction, or qualify an equivalent atomic CAS.
   Erasure, owner/session loss and invalidation must win against delayed work.
   A helper call followed by the existing unconditional write is insufficient.
   The current read shape has no proof-instance revision token. A CAS design
   must add and bind one, including delete/recreate races; matching a later
   classification to an earlier snapshot is insufficient.
3. Qualify mixed old/new writers and ongoing goals, then retire every old worker
   that still reads plaintext provenance. Preserve historical workflow command
   names and payload compatibility. A read-only seam does not qualify retirement.
4. Qualify the new verifier with legacy gaps, invalidation, ownership, erasure,
   completion races and held-action recovery. Keep consequential-action status
   verification on its existing authoritative ledger.
5. Design encryption/redaction, shared forgetting and retention as a separate
   migration. Changing semantic storage intentionally invalidates the current
   projection; any replacement proof or preservation rule needs its own reviewed
   contract rather than an inferred exception to the trigger.

No backfill, worker cutover, production migration, deployment, source-content
redaction or retention change is performed by this source work.
