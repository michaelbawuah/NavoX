# SPEC-002 Milestone 1 — Contracts and Persistence

Milestone 1 establishes the provider-independent intelligence boundary described
by SPEC-002 without changing SPEC-001 execution authority.

## Canonical contract

Every future authorized connector must normalize retrieved information to
`SourceDocument`. The contract is versioned as `source-document.v1`, rejects
unknown fields, requires timezone-aware timestamps, normalizes time to UTC, and
restricts metadata to JSON-safe values.

`SourceIdentity` carries only identity data. Neither contract contains
permissions, approvals, action authority, credentials, or provider tokens.

## Persistence

The migration `0010_spec_002_m1_intelligence` adds:

- `people`: canonical workspace-scoped people;
- `person_identities`: provider/identity mappings with workspace-scoped
  uniqueness on identity type + value;
- `operational_observations`: proposed facts with person links, confidence,
  extractor version, and optional model provenance;
- `observation_evidence`: bounded source provenance tied to an authorized
  `connections` row and an observation;
- `intelligence_feedback`: workspace/user-scoped feedback with no execution
  authority.

Observation and identity confidence use `NUMERIC(4,3)` to preserve calibrated
values without binary floating-point drift.

## Isolation and provenance invariants

1. People and identities belong to a workspace.
2. Observations belong to both a user and workspace.
3. Evidence cannot exist without both an observation and an authorized
   connection.
4. Reprocessing the same evidence tuple is constrained by an idempotency unique
   key.
5. Feedback has no foreign key to actions, approvals, or permissions.
6. Deleting a workspace cascades its people, identities, observations, and
   feedback through their tenant roots; person references on observations are
   nulled when an individual person record is removed.

## Deliberately deferred

M1 does not retrieve Gmail or Calendar content, invoke an extraction model,
resolve temporal expressions, merge commitments, infer completion/waiting
state, rank attention, or learn preferences. Those belong to later SPEC-002
milestones.
