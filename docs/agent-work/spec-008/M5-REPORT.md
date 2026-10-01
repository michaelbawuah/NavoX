# SPEC-008 M5 — read-only subscription answers

Status: local slice on `spec-008-navoxbot`; uncommitted and unpushed.

The existing SPEC-005 structured-intent planner has a new versioned v2 prompt
and schema for `subscription.search`; the registered v1 artifact remains
unchanged. The plan can only name a route and grounded entity, never issue a
cancellation instruction or approve an action. The TypeScript runtime verifies
that the named entity appears in the user's own question before searching.

The runtime delegates only through the authenticated SPEC-004
`POST /subscriptions/query` SEARCH endpoint, using the entity as a bounded
substring selector. It asks for clarification when the result has zero or
multiple candidates. For one candidate, it rechecks the account scope and
reads `GET /subscriptions/{id}/cancellation`. It validates the returned ID,
scope and revision against the selected subscription before reporting
cancellation status. A recorded renewal is separate from an access-end date;
the latter remains unknown unless an estimate is explicitly labeled as an
unverified provider preview. No cancel, confirm, approval or provider mutation
is routed from this capability.

TypeScript tests cover route resolution, grounded selectors, zero/multiple/one
match, missing renewal, verified versus pending cancellation, preview labels,
changed account, stale source revision, malformed responses, source outages,
gateway cookie/path forwarding and zero action references. A Web rendering
test covers the same distinction. The full PostgreSQL/Temporal API gate on
the M5 planner tree passed: **2,577 API tests, zero skipped**, plus Ruff,
strict mypy, Alembic SQL/schema, connector/subscription metrics, deterministic
release evaluation and whitespace checks. Its log is
`/tmp/navox-spec008-m5-api-gate.log`. Root JavaScript lint, typecheck, tests
and Web build passed; 282 Web tests, 174 runtime tests with five database-only
skips and eight extension tests passed. Against a separate migrated disposable
PostgreSQL database, the runtime suite passed **179/179 with no skips**. No
live provider call, catalog publication or paid setup was made.

Still open: live authorized subscription/provider observation, broad SPEC-008
scenario F acceptance, News and other domains, multi-intent, voice/wake,
activity/Temporal, and hosted CI on an exact published head.
