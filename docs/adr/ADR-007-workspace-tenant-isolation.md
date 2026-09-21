# ADR-007 Workspace Tenant Isolation

## Status

Accepted

## Context

NavoX begins with personal workspaces but will support teams later. Every operational record must be protected from cross-tenant access from the first schema and API implementation.

## Decision

Make workspace identity an explicit ownership boundary. Tenant-owned database records include a `workspace_id`; workspace membership mediates access; all queries are scoped to the authorized workspace. Client-supplied user identity or object UUID knowledge is never sufficient authorization.

## Consequences

- Data model and migrations include workspace keys and foreign keys from the outset.
- Repository, API, workflow, and event-ingestion tests must include cross-tenant failure cases.
- This introduces early schema discipline but avoids an unsafe later migration.
