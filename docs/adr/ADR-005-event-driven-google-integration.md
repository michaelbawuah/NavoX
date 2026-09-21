# ADR-005 Event Driven Google Integration

## Status

Accepted

## Context

Repeated full LLM scans of a user’s Google data are costly, stale, and privacy-unfriendly. Provider changes should lead to deterministic, auditable state updates.

## Decision

Use authenticated Google events/watches where supported. Ingestion verifies ownership from the authenticated provider connection, deduplicates and normalizes payloads into canonical `incoming_events`, then invokes processors and the Proactive Engine. Provider watch/subscription renewal is a durable workflow.

## Consequences

- The system processes changes instead of polling everything repeatedly.
- Malformed, duplicate, replayed, and cross-tenant event tests are mandatory.
- Watch expiration and renewal are first-class reliability concerns.
