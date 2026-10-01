# SPEC-008 M5 — read-only subscription answers

Status: next implementation brief; no M5 code or acceptance claim yet.

The PDF's next fast-track domain is subscriptions. Add `subscription.search` to
the registered SPEC-005 structured-intent vocabulary (the existing Python
gateway is the bounded dependency exception) and to the TypeScript capability
registry. The planner may only propose a route and grounded entity text, never
a cancellation command. TypeScript must verify that the named merchant/entity
is present in the user's utterance before using it as the search selector.

Delegate read-only through the authenticated SPEC-004
`POST /subscriptions/query` SEARCH endpoint. Its `text` is a substring filter,
so do not send the full natural-language question as the selector. Require one
exact candidate for a named-merchant answer; zero or multiple candidates ask
for clarification. Recheck the current session scope before the service call.
For one candidate, use the existing
`GET /subscriptions/{obligation_id}/cancellation` read-only endpoint to report
verified cancellation state alongside `next_renewal_at` when present. Keep
renewal and access end distinct: `preview.access_ends_at` is a provider preview,
not a verified access-end fact, and must be labeled as such or withheld. If an
actual access end cannot be established, say it is unknown. No cancel, confirm,
approval or provider mutation is in this bundle.

Validate and bound all SPEC-004 responses in TypeScript, preserve exact record
IDs and currentness/verification labels, never invent a date from status, and
return a qualified failure on malformed, inaccessible or changed source data.
Add runtime, gateway, contract and UI tests for unique/ambiguous/missing match,
missing renewal, cancellation preview versus verification, changed account,
source outage and zero action calls. Run focused checks, then the full API gate
with disposable PostgreSQL/Temporal because the existing SPEC-005 planner
schema changes, plus root JS lint/typecheck/tests and Web build. Do not publish
the provider catalog or make a live paid call as part of this bundle.
