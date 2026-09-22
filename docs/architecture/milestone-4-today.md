# Milestone 4: Today implementation plan

SPEC-001 remains authoritative. Today projects saved operational state into four
sections: Needs Attention, Coming Up, Money/Renewals, and Waiting On. Dashboard
and conversational queries use the same backend service and fresh database reads.

## Projection rules

- Exclude completed, rejected, expired, and not-yet-valid commitments.
- Candidates stay explicitly unconfirmed and are surfaced for review.
- Needs Attention includes candidates, overdue/due-today work, explicit attention
  state, and high-priority undated work. Coming Up covers other active work.
- Renewal and waiting views can overlap the primary attention/upcoming views;
  the total counts distinct commitments, never the sum of section counts.
- Due-today uses a validated IANA timezone (browser supplied, account fallback).
- Scores and reasons are deterministic presentation rules, not notification or
  execution authority. Full proactive scoring remains Milestone 7.
- Include saved source references and currently implemented capabilities only.

## Usable first loop

Add manual commitments with explicit user provenance, review extracted candidates,
mark confirmed work as waiting, resume it, or mark it complete. Validate transitions
in SQL under tenant scope; repeated completion is idempotent. Manual creation uses
a request UUID scoped to the workspace to prevent duplicate retry inserts.

The existing Google connection grants identity access only. Do not imply inbox
discovery or automatically expand scopes. No fabricated items or completion stats.
The existing extraction boundary has no live model adapter; Today reads whatever
commitments have actually been saved.

## Conversation

Read-only bounded intent routing answers today, attention, this week, waiting,
renewals, and promises queries from the projection. Unsupported requests explain
the available queries. External content is displayed as plain text and never
interpreted as commands. Chat is not an execution interface.

## Verification

Test auth/tenant isolation, midnight and timezone behavior, category overlap,
candidate review, validity, completion freshness, idempotent creation, and invalid
transitions. Verify the complete create -> Today -> query -> complete -> updated
Today loop against PostgreSQL in Compose CI. Check desktop/mobile layout and
keyboard controls, network failure states, and escaping of source text.
