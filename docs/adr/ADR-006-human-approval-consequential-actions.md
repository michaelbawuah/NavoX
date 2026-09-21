# ADR-006 Human Approval for Consequential Actions

## Status

Accepted

## Context

External actions can have consequences beyond a user’s ability to easily reverse them. An LLM must not grant itself authorization.

## Decision

Classify tools/actions with a deterministic R0-R5 risk model. R3 external actions require human approval of the exact canonical security-relevant payload. Persist its hash and expiry. Material payload changes invalidate approval. The Tool Gateway is the only component allowed to execute provider calls.

## Consequences

- Initial Gmail send is an R3 flow: prepare, persist, approve, payload-hash verify, execute, verify, audit, and update the commitment.
- Tests must show that bypass, tampering, duplication, and escalation fail closed.
- The user can pause NavoX so pending approvals do not execute unexpectedly.
