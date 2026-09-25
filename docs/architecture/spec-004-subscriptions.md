# SPEC-004: subscriptions and recurring obligations

Implementation of the supplied **SPEC-004 NavoX Subscription & Recurring
Obligation Intelligence** specification. The internal domain is
`RecurringObligation`; the authenticated product page is `/subscriptions`.

## Product behavior

The registry accepts manual subscriptions and evidence discovered from authorized
Gmail and SPEC-003 source ingestion. The existing Chrome extension's explicit
page-note import can supply subscription evidence through the same connector
ingestion path. It does not read page bodies or browse account pages automatically.
Uncertain amounts, currencies, dates and identities remain candidates for review.
Marketing copy, newsletter subscription text and one-time receipts are excluded.

Repeated source revisions are deduplicated; source account and plan identity
preserve separate subscriptions. Manual corrections append `USER_OVERRIDE`
evidence, with only the explicitly changed fields authoritative. Historical
prices retain their effective intervals, including announced future prices.
Temporal reevaluation applies a supported future price when its effective date
arrives. User price corrections remain authoritative.

Costs use Decimal/NUMERIC and preserve their original currency and billing
interval. Monthly/yearly equivalents are estimates of known recurring costs,
with each currency totaled separately. Unknown prices remain unknown. A trial
ending does not prove payment or conversion.

Renewal, trial, price and cancellation events project into the existing SPEC-002
commitment and attention system. Keep suppresses an unchanged cycle; a material
change invalidates that suppression. Snooze and Not My Subscription are owner
decisions. The dashboard and Today share these decisions.

## Cancellation boundary

`subscription.cancel` is R4 and is separate from `email.unsubscribe`. Clicking
Cancel prepares a preview and starts a durable workflow. It does not authorize
the provider write. Confirmation names one attempt and its exact payload hash.

The preview includes the freshly inspected provider plan, amount, currency,
interval and renewal, alongside expected effect, access end, fees, refunds and
explicit unknowns. Provider economics can differ from an older registry entry;
the review renders the inspected values and binds them into approval. A changed
provider revision, economics, target, policy, grant or registry revision prevents
the old preview from executing. The generic action-approval route cannot approve
this action.

Before submission, the engine commits an `IN_PROGRESS` execution fence and
consumes the approval. A timeout, lost response or worker interruption directs
recovery to independent reads; it never blindly resubmits. Temporal submission
activities have one attempt. Idempotency keys and provider revision preconditions
also bind the exact provider write.

Submission is not success. `CANCELLED` requires fresh independent provider-state
evidence for the dispatched account/subscription, with cancelled state and
auto-renew disabled. Confirmation emails and user reports can be evidence but
cannot manufacture this verified result. Verification has a durable bounded
read budget; unknown outcomes remain pending and visible. MFA/CAPTCHA requires
the user to continue at the provider. Genuine later renewal evidence contradicts
the earlier result and schedules a new verification budget; delayed old receipts
do not automatically contradict it.

The eight named workflows in SPEC-004 are registered with the existing worker.
A durable reconciliation workflow recovers committed dispatch/outbox gaps and
works when the AI provider is disabled. Workflow arguments contain identifiers,
not provider credentials or private source documents.

## Reviewed provider capability

Deployment setting `SUBSCRIPTION_CANCELLATION_PROFILES` defaults to `[]`.
An empty setting exposes the registry and lifecycle features, while cancellation
honestly shows an unsupported/manual fallback. It does not claim universal
merchant cancellation support.

An operator-reviewed REST profile supplies:

- A profile ID and existing approved `GENERIC_REST_CONNECTORS` configuration ID.
- The exact HTTPS origin, merchant domain and bounded account/inspect/cancel paths.
- State field mappings and allowed status mappings, known expected effect, and an
  optional management path on the same reviewed origin.
- Provider support for idempotency keys and revision preconditions.
- An explicit enable flag. No credentials belong in the profile.

Paths for subscription operations contain exactly one `{subscription_id}`.
The provider account endpoint must authenticate the token and explicitly declare
`subscription.cancel`. The inspect endpoint must independently identify the
same account and subscription, current revision and whether cancellation is
allowed. Execution sends `Idempotency-Key`, `If-Match`, the exact subscription ID
and expected revision. Verification performs a separate GET. Untrusted text
cannot choose an origin, path, credential, capability or profile.

The owner first connects through SPEC-003's existing credential flow. In
subscription details they explicitly enable cancellation capability for one
reviewed profile and bind an exact provider subscription. The binding endpoint
independently inspects that provider target. This connection-level grant is
bound to the profile/configuration digest and credential version; it is not an
approval to cancel any subscription. Every cancellation still requires its own
exact-action review.

The capability uses the existing connector ownership checks, capability gateway,
secret broker and approved HTTPS transport. Responses are bounded and sanitized;
redirects and credential-bearing response data are rejected. No new dependency
or merchant-specific branch is introduced.

The disposable provider protocol is executable in
`services/api/integration/subscription_temporal_smoke.py`. Its `.example` domain
and synthetic credentials are fixtures, not a real service configuration. A live
merchant integration requires review of that merchant's actual supported API.

## API and storage

Migration `0023_recurring_obligations` adds merchants, aliases, obligations,
obligation evidence, price history, cancellation attempts, cancellation evidence
and a domain outbox. Money is `NUMERIC(18,4)`. Records and every command are scoped
to both workspace and user. Evidence links to the existing legacy provenance
anchor; native connector IDs are normalized through owned connections.

All specified registry and cancellation endpoints live under `/api/v1`.
Additional endpoints support explicit review decisions, bounded read queries,
reviewed profile inventory, separate provider grants and target binding.
Mutation routes enforce the application's Origin policy. Cookies, provider
tokens and arbitrary SQL are not query inputs.

`GET /subscriptions/prevented-renewals` reports **Unwanted renewals prevented**:
explicitly confirmed cancellations independently verified before the scheduled
renewal. The same obligation/cycle is counted once. Amounts come from the exact
inspected preview and remain separate by currency. Unknown charges stay unknown;
contradicted results stop counting. This is not a claim of refunds, lifetime
savings or guaranteed future cash flows.

Connector source erasure also removes dependent subscription evidence, copied
snapshots, prices, cancellation history and Today projections. Surviving state
is rebuilt solely from independent evidence and actual user corrections.
Ambiguous or cross-owner provenance aborts the transaction. An active external
cancellation retains its execution fence and blocks erasure until resolved or
withdrawn before submission.

## Verification and remaining live acceptance

`bash scripts/check-api.sh` now requires the SPEC-004 labeled-document evaluation,
safety regressions and all eight migration tables, alongside every pre-existing
gate. The discovery corpus separates development and holdout examples and reports
actual labeled numerators/denominators. Small fixture rates are not estimates of
real-mail accuracy or merchant coverage.

Hosted API CI additionally runs registry, discovery, cancellation, capability,
lifecycle, API and erasure tests against disposable PostgreSQL schemas. Compose
CI exercises real API authentication, connector grant, exact preview approval,
Temporal execution, independent verification and the prevented-renewal metric
against a disposable provider. It verifies one provider write and an independent
post-write read. Existing SPEC-002 and SPEC-003 checks remain required.

Live mailbox precision/recall targets and a real disposable merchant cancellation
must be measured separately. No live subscription is cancelled by these tests.
SPEC-004 Phase 11's browser execution remains dependent on a mature, authorized
SPEC-003 browser capability. The current explicit page-note import provides
browser-assisted registration evidence, not a browser cancellation executor.
These boundaries must remain visible in release and acceptance claims.
