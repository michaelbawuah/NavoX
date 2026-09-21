# ADR-001 PostgreSQL Operational System of Record

## Status

Accepted

## Context

NavoX needs durable, queryable, tenant-scoped operational state for users, workspaces, commitments, plans, actions, approvals, notifications, incoming events, workflow references, and append-only audit events.

## Decision

Use PostgreSQL as the MVP operational system of record. Model the Commitment Graph with relational tables, foreign keys, indexes, JSONB only where semi-structured data is appropriate, controlled enums, timestamps, deletion policies, and idempotency constraints.

## Consequences

- Transactions and tenant-scoped queries are straightforward.
- The system avoids prematurely operating a separate graph database.
- Complex graph traversal may be less expressive than a dedicated graph store; revisit only when query evidence justifies it.
