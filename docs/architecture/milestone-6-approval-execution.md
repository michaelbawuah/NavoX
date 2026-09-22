# Milestone 6: Approval and verified execution

SPEC-001 remains authoritative. Milestone 6 introduces the first consequential
external action while preserving the invariant that models and external content
cannot grant authority.

## Scope

The first executable R3 action is **Gmail send**. Calendar and Drive mutations
remain disabled. Gmail read access is not introduced.

The user flow is:

```text
active commitment
  -> prepare exact Gmail action
  -> persist action + canonical payload hash + approval vN
  -> durable Temporal wait
  -> user approves / rejects / revises
  -> execution-time policy revalidation
  -> consume approval exactly once
  -> Gmail send
  -> verify provider message ID
  -> audit + commitment state update
```

## Incremental Google permission

Google identity remains the default connection contract. NavoX requests
`https://www.googleapis.com/auth/gmail.send` only through a separate
connection-specific capability grant initiated from the signed-in workspace.

The upgrade attempt is PKCE/state protected, tenant scoped, bound to the exact
stored Google connection, and accepts only NavoX's allowlisted scopes. Refresh
tokens remain encrypted in the existing credential vault.

Permission is necessary but never sufficient. Gmail send is R3 and always
requires exact-action approval.

## Exact-action approval

Each prepared Gmail send persists:

- provider and action type;
- connection ID and verified sender;
- recipient;
- subject;
- exact plain-text body;
- post-send commitment transition;
- canonical security hash;
- risk level R3;
- versioned approval with expiry;
- idempotency key and workflow reference.

Approval TTL is 15 minutes. Editing a pending action supersedes the current
approval and creates a fresh version/hash. A decision request UUID is
workspace-unique so retries are idempotent and cannot be replayed against a
different action or opposite decision.

The approval engine never trusts a browser-supplied risk value or payload hash.

## Execution-time checks

Before provider access NavoX revalidates:

1. action belongs to the user/workspace;
2. action contract still exists and is R3;
3. latest approval is approved and unexpired;
4. approval hash equals the stored action hash;
5. the canonical action payload recomputes to the same hash;
6. exact Google connection is active and tenant-owned;
7. `gmail.send` scope is still granted;
8. stored sender matches the connected Google account;
9. agent is not paused.

Any mismatch fails closed.

## One-time approval consumption

The action row and latest approval row are locked before crossing the external
side-effect boundary. Exactly one caller may transition:

```text
approved -> executing
approval approved -> consuming
```

Concurrent executors observe the persisted transition rather than issuing a
second provider request.

If NavoX is paused before consumption, the approved version is superseded and
a new pending approval is required after resume.

## Gmail at-most-once policy

Gmail `users.messages.send` accepts an RFC MIME message encoded in base64url
as `raw`, but exposes no provider idempotency key. Therefore NavoX does not
automatically retry the provider send after the action has crossed into
`executing`.

Outcomes:

- Gmail returns a message ID -> action is `completed` and `verified_at` is set.
- Failure before execution marker -> bounded workflow recovery may retry safe
  pre-execution work.
- Failure after execution starts / ambiguous transport outcome -> action becomes
  `uncertain`, plan becomes `manual_review`, and NavoX instructs the user to
  inspect Sent mail rather than risking a duplicate.

## Durable Temporal workflow

`ApprovedActionWorkflow` is keyed by `navox-approved-action-{action_id}`.
It waits durably for authorization state and receives approval decision signals.
PostgreSQL remains the authorization source of truth; the workflow also polls
persisted state so a lost signal cannot lose or invent a decision.

The provider execution activity has one attempt. If the activity fails after
the action was marked executing, a separate safe activity records the uncertain
outcome instead of retrying Gmail.

## UI

The signed-in Today workspace includes an External Action card with:

- incremental Gmail permission gate;
- commitment and Gmail-account selection;
- recipient, subject, body, and post-send state;
- exact payload preview;
- R3 badge and approval expiry;
- payload fingerprint;
- revise / reject / approve controls;
- verified Gmail message receipt;
- explicit manual-review state for uncertain outcomes.

The UI is presentation only. Server policy, hashes, locks, permissions, and
approval state are the authority boundary.

## Verification

Automated coverage includes:

- Gmail scope required before preparation;
- preparation never sends;
- exact approval and provider verification;
- duplicate execution suppression;
- payload tamper blocking;
- ambiguous provider outcome with no retry;
- pause invalidation and fresh approval requirement;
- edit supersedes old approval and changes the hash;
- approval expiry;
- opposite-decision request-ID replay rejection;
- cross-tenant action isolation;
- MIME/base64url construction;
- incremental OAuth scope validation;
- real Compose Temporal prepare -> wait -> reject lifecycle.

Real Gmail delivery is intentionally not performed in CI. Provider execution is
covered with a deterministic fake gateway; production execution remains behind
the real Google OAuth capability grant.
