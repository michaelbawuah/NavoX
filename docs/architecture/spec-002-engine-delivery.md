# SPEC-002 operational intelligence delivery

Continue from M1 on main and the M2 extraction implementation in PR #10.
Preserve SPEC-001 authentication, exact-action approvals, execution and UI.

## Implementation sequence

1. Harden extraction validation and provider failures; keep fixture evaluations
   distinct from live model quality measurements.
2. Resolve exact identities, supported temporal expressions and evidence-backed
   commitments deterministically. Preserve uncertainty and user decisions.
3. Normalize authorized incremental Gmail and Calendar reads. Store cursor and
   bounded evidence, never a mailbox archive. Failed batches retain their cursor.
4. Infer completion and waiting conservatively; sending a request is not proof
   that its desired external outcome occurred.
5. Compute explainable attention from structured state, with confidence ceilings,
   fatigue and waiting policies. Feed the existing Today experience.
6. Persist idempotent feedback and bounded ranking preferences. Reconciliation
   retries processing through Temporal; no workflow contains raw source bodies.

## Integration boundaries

`process_connection` retrieves a bounded incremental batch under the active
connection's granted read scopes. `OperationalExtractor` validates proposals.
`resolve_extraction` verifies provenance again and mutates operational facts in
the caller's database transaction. Attention and feedback affect presentation,
never action permissions. PostgreSQL remains the user-facing source of truth.

Required verification includes replay, cursor failure recovery, tenant isolation,
ambiguous dates, source corrections, explicit completion/approval responses,
marketing suppression and malicious source content. Provider fixtures verify
adapter behavior; live account/model validation is reported separately.
