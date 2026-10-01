# M14B — bounded personal goals and durable verification

Baseline: `303d77889e4f2916f177b0393eb295bb8ede89d7` on
`spec-008-navoxbot`. Draft PR #22 stays draft. SPEC-008 R3 requires TypeScript
Temporal workflows; this phase adds only the personal workflows needed for
briefing, meeting prep and consequential-action verification.

## Contract and security decisions

- Reuse existing SPEC-002 Today, proactive meeting-prep and SPEC-001/003 action
  services. No AI or connector provider is called by the Temporal worker, and
  the worker never executes or approves a consequential action.
- A goal has one bounded kind: `BRIEFING`, `MEETING_PREP` or
  `COMMUNICATION_ACTION`. Its durable identity is an opaque UUID. Its payload
  contains only identifiers for the owning AssistantSession and saved turn or
  SPEC-001 action. It carries no browser cookie, email body, source content,
  provider token or approval secret into Temporal history.
- Goal creation happens only after the existing authenticated runtime has
  derived and checked user/workspace scope. Recheck that scope when reading or
  mutating the goal. The worker reads its own goal row by opaque ID from the
  same PostgreSQL database and rechecks that any referenced turn/action is
  owned by the stored user and workspace before changing status.
- Briefing/meeting workflows verify that an existing saved turn is a grounded
  successful result. They track the already-built service output; they do not
  duplicate its retrieval or generation. Communication workflow observes the
  existing action ledger and may mark `COMPLETED` only for an action whose
  status is `completed` and whose `verified_at` exists. Pending approval stays
  `WAITING_FOR_USER`; executed but unverified stays `WAITING_FOR_EXTERNAL`.
  Failed/blocked/cancelled/uncertain records never become success.
- Bound plan steps, retries, workflow lifetime and polling. A retry or replay
  must not start a second action or create duplicate goals. A workflow outage
  must leave a truthful persisted pending/failed state and a recoverable
  dispatch path. Do not claim durable completion merely because Temporal
  accepted a start request.
- Existing assistant routes keep origin, authentication, request idempotency,
  session ownership and exact approval behavior. Workflow code grants no new
  action authority. Scope is personal only; no Team feature.

## Execution bundle

Implement the smallest TypeScript Temporal worker/client and shared goal
contract in the existing stack, plus scoped persistence, runtime wiring and
Next API/UI exposure needed to inspect a goal's truthful state. Keep new
configuration narrowly scoped to the assistant queue and worker. Add
regressions for cross-user/workspace access, duplicate dispatch, verification
chronology, pending approval, uncertain/failure, replay and worker outage.
Include a disposable PostgreSQL/Temporal integration check. Update this
phase's worker report. Do not commit, push, publish provider catalog, call
paid providers, send mail or touch unrelated SPEC-006/007 files.

Root review will inspect tenancy, Temporal payloads/history, status authority,
retry bounds, migration consistency and the full pre-push gates before any
commit. Live connected/provider acceptance remains a later gate.
