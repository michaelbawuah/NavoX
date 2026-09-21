# ADR-003 Minimal Storage Privacy

## Status

Accepted

## Context

NavoX derives value from Gmail, Calendar, and Drive but does not need to become a duplicate mailbox or document store. Broad data retention increases privacy and security risk.

## Decision

Store normalized operational facts, source references/external IDs, extracted commitments, useful metadata and hashes, memory with provenance/validity, plans, actions, approvals, and audit state. Retrieve raw source content from the authorized provider only when necessary. Store credential references rather than plaintext OAuth refresh tokens.

## Consequences

- Storage is smaller and more privacy-aligned.
- Source systems remain authoritative.
- Read paths must handle provider availability, scope changes, and revoked connections.
