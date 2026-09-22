# Milestone 5: Bounded Agent implementation

SPEC-001 remains authoritative. This milestone turns **Handle This** into a
durable, inspectable execution path without expanding Google scopes or granting
the planner authority over risk.

## Runtime flow

```text
authenticated commitment
  -> bounded Context Builder
  -> deterministic plan (maximum 8 steps)
  -> persist plan + ordered steps
  -> dispatch one Temporal workflow with a stable business workflow ID
  -> re-evaluate policy before every step
  -> execute only permitted R0/R1 internal actions
  -> persist action result + audit event
  -> finalize plan from persisted step state
```

PostgreSQL remains user-facing operational truth. Temporal owns retries and
durable workflow execution. REST creates/queries work; it does not run a
long-lived agent inline.

## Context boundary

The Context Builder reads only the selected tenant-scoped commitment and the
smallest relevant saved state: its objective, at most 12 provenance references,
at most 12 commitment relations, and connection capability metadata. Raw OAuth
credentials and a permanent chat transcript are never placed into plan context.
The canonical context snapshot is hashed and persisted with the plan.

External text remains data. The current deterministic planner never interprets
source text as instructions.

## Planning invariants

- Maximum plan steps: **8**.
- Replan design limit: **2**; no autonomous replanning is enabled yet.
- Current planner version: `deterministic-v1`.
- A client-supplied goal can describe intent but cannot choose actions, providers,
  permissions, or risk levels.
- Plan request UUIDs are unique per workspace, making retries idempotent.

The current safe plan is intentionally small:

1. `navox.commitment.inspect` — R0 database read.
2. `navox.context.prepare` — R1 preparation from the hashed context snapshot.
3. `navox.next_steps.prepare` — R1 preparation of non-executing next-step options.

## Action contracts and policy

Risk is defined in the server-side action registry, never by planner/model
output. Each contract owns provider, permissions, risk, idempotency policy,
timeout, and verification method.

Milestone 5 automatic execution is limited to R0 and R1 **and** to actions with
an implemented Milestone 5 executor. Gmail, Calendar, and Drive contracts are
registered now so their risk cannot later be improvised. Their executors remain
disabled and the current Google connection is identity-only.

- R0/R1 internal actions: may execute when policy allows.
- R0/R1 provider actions: fail closed until an executor and exact permission are
  deliberately enabled.
- R2+: never executes in Milestone 5. Approval/execution begins in Milestone 6.
- Unknown action names: blocked.
- Stored risk that differs from the contract: blocked.
- Changed payload hash: blocked.
- Paused agent: blocked at plan creation and again before every action.

## Persistence and audit

Migration `0006_bounded_agent` adds:

- `plans`
- `plan_steps`
- `actions`
- `workflow_refs`
- `audit_events`

All user-facing agent records carry workspace/user boundaries where applicable.
Plans retain the context hash and planner version. Actions retain immutable
contract risk, payload hash, idempotency key, policy reason, and result.

## Temporal reliability

`HandleCommitmentWorkflow` receives only persisted plan/step IDs. Each step is
an activity with retries and reloads authoritative database state before
execution. The active workflow has a five-minute execution timeout and each
safe step has a bounded activity timeout. A stable workflow ID
(`navox-handle-plan-{plan_id}`) protects dispatch retries from starting a
second business workflow.

The Compose stack runs a dedicated worker service using the same task queue as
the API dispatcher.

## Product experience

Eligible commitments expose **Handle this**. The signed-in workspace shows:

- global agent Ready/Paused state;
- current plan and terminal failure state;
- deterministic planner version;
- step usage against the eight-step budget;
- replan counter;
- per-step action name, risk level, and persisted status;
- recent plans.

The UI is not authority: server policy remains the enforcement boundary.

## Verification

Tests cover:

- retry-safe plan creation;
- safe R0/R1 execution only;
- plan/action/audit persistence;
- agent pause enforcement;
- terminal commitment rejection;
- cross-tenant plan isolation;
- risk-level tampering;
- unknown actions;
- missing provider permissions;
- unavailable provider executors;
- R3 approval requirement;
- the complete Docker Compose path through a real Temporal worker.

Milestone 6 will add approval records, approved-payload hash validation, Gmail
send execution, independent provider verification, and duplicate/tamper/bypass
tests for consequential R3 actions.
