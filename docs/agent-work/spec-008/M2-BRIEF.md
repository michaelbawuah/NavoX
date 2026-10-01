# SPEC-008 next dependency bundle — structured intent bridge

Status: planned, not implemented. Start only after the M1 correction review and
the current red hosted CI diagnosis remain documented.

## Why this bridge is necessary

SPEC-005 currently exposes `/ai/operations/assistant` for selecting facts from
an explicit set of saved item IDs. Its response is not a general intent plan,
and it cannot classify a free-form request such as “What did Sarah email me?”
or “When does Netflix renew?”. SPEC-008 must not infer those routes by growing
a string-matching router. A minimal addition to the existing Python SPEC-005
gateway is therefore the existing-dependency exception in the owner's R3
language decision. The public assistant API, planner validation, capability
resolution, orchestration, state and contracts remain TypeScript.

## Proposed bounded contract

An authenticated internal SPEC-005 endpoint accepts the current utterance and
at most four recent turn references, with strict length and count limits. It
returns a structured, provider-neutral intent plan with at most four intents,
each carrying a route enum, entity/time/reference slots, confidence and a
clarification requirement. It never returns a tool URL, approval grant or
executable action. SPEC-005 owns provider policy, cost, fallback, schema
validation and task audit. If no qualified model/provider is available, the
assistant returns an honest unavailable/clarification response; it never falls
back to keyword guesses.

The default-off local deployment keeps its current Today behavior and makes
no paid call merely because a user opened `/navox`. The TypeScript runtime may
ask the read-only SPEC-002 Today classifier first; only an `unsupported`
answer proceeds to SPEC-005 intent planning. This ordering never turns source
text into tool authority. This bundle does not activate a provider, grant,
catalog revision or deployment feature flag.

The TypeScript runtime validates every field against a server-owned registry,
rechecks the current authenticated scope, binds any follow-up reference to a
session-owned selector, and resolves read-only capabilities before introducing
consequential actions. The first added route uses existing SPEC-007
search/evidence to resolve an email/person/thread with exact selectors and
currentness. The following action bundle then uses the existing
`/communication-drafts` and SPEC-001/003 approval/execution APIs. There is
never a direct send from a transcript. SPEC-004 subscriptions follow after
email and meeting preparation,
matching R3's order. The owner-superseded X route stays absent.

## Acceptance for this bundle

- Unconstrained wording is parsed by SPEC-005 into a typed plan; unknown route,
  altered workspace, oversized/malicious slots, and fake authority are rejected
  by the TypeScript boundary.
- Read-only SPEC-007 email resolution returns source-grounded or qualified
  failure responses, including ambiguity. A no-provider deployment remains
  honest and useful through the existing Today path.
- A multi-intent or action proposal cannot execute an action in this bundle.
- The exact API gate, web checks, migration consistency, and hosted CI are
  required before declaring the checkpoint complete.

## Ownership and stop conditions

The Flash implementation worker may own a minimal SPEC-005 intent contract,
prompt/schema registration, authenticated gateway route and focused Python
tests inside `services/api`; the TypeScript shared contracts, runtime
planner/capability adapter, Next assistant routes and focused tests; and one
phase report. Existing SPEC-001–007 decision, source and action logic remains
owned by those services. Public assistant API handlers remain TypeScript. Do
not edit deployment flags, `.github/workflows`, `AGENTS.md`, M1 reporting or
unrelated SPEC files. Do not commit, push, activate providers or make paid
calls.

If the SPEC-005 registry cannot represent this bounded task without changing
its safety evaluation and policy rules, stop and report the exact dependency
instead of introducing an unregistered provider call or a keyword router. No
email send or approve action is in this bundle; source resolution and planning
precede that action phase.
