# ADR-002 Temporal Durable Workflows

## Status

Accepted

## Context

NavoX work spans long waits, approval pauses, retries, timers, follow-ups, provider-watch renewals, and recovery from failures. A request-response worker is insufficient for this operational lifecycle.

## Decision

Use Temporal with the Python SDK for durable workflow execution. PostgreSQL remains the user-facing operational truth. Temporal workflow references are persisted so entities can be traced to their workflow/run state.

## Consequences

- Workflows survive process failures and can wait for days.
- Signals support approval, rejection, modification, external response, completion, and pause events.
- Local development and deployment require a Temporal service and worker lifecycle.
