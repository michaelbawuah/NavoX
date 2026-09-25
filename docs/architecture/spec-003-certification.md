# SPEC-003 certification evidence and limits

Run the fixture-backed connector gate from the repository root:

```bash
bash scripts/certify-connectors.sh /tmp/navox-connector-certification.xml
```

The command fails on any failed test and writes JUnit XML with the exact test
names and counts. It combines the contract, capability, tenant, credential,
runtime, sync recovery, event subscription and ingress, Google, Canvas, import,
generic REST, MCP, unknown-service, and connection management suites. It is
additive to the required full API gate
(`bash scripts/check-api.sh`) and hosted CI on the exact proposed commit.
`NAVOX_CONNECTOR_TEST_DSN` can point the portability tests at a disposable
PostgreSQL database; otherwise they create temporary SQLite databases.

`test_connector_certification.py` is a common probe applied to seven reference
manifests (Google Workspace, Gmail, Calendar, Canvas token and OAuth, imports,
and configured REST). It checks strict manifest round trips and rejected
authority fields/API version drift, the three independent permission grants,
unhealthy state, and canonical schema, provenance, stable identity, isolation,
and read-only write denial across Google Calendar, Canvas, ICS, CSV, JSON, and
configured REST fixture resources. The provider-specific suites exercise
operational behavior that cannot be inferred from a manifest.

| SPEC-003 gate | Reproducible evidence | Scope of conclusion |
| --- | --- | --- |
| Contract and canonical validity | Common probes and provider-specific sync tests | Constructed reference fixtures validate against current contracts; arbitrary external adapters require their own contract run. |
| Workspace and capability isolation | Runtime, capability, secret broker, managed Canvas/import, and portability suites | Known attack cases deny access before provider/model I/O. No production penetration test was performed. |
| Secret boundary | Broker, lease lifecycle, generic REST reflection, Canvas reflection, and portability tests | Synthetic credentials remain out of persisted/model inputs in tested paths. Deployment log/front-end audit remains separate. |
| Cursor recovery and replay | Sync recovery and provider pagination suites | Tested crash/retry/replay schedules and deterministic provider fixtures. Production rates cannot be inferred from this count. |
| Portability A/B/C | Google bridge, managed Canvas, managed imports, and certification probes | Google compatibility and Canvas/import canonical paths are fixture exercised. |
| Portability D | `test_connector_portability.py`, `test_connector_mcp.py`, `test_mcp_management.py` | Unknown, random REST provider reaches SPEC-002 and Today without core changes in a configured mock demonstration. MCP discovery and reads use approved fixtures. A **live unknown service** is still required by the spec. |
| Event subscription lifecycle | `test_connector_subscriptions.py` | Fenced registration, renewal, cancellation, and cleanup retries are fixture exercised; no production adapter implements these provider event contracts yet. |
| Authenticated event ingress | `test_connector_events.py`, `test_events.py` | The generic receiver bounds streamed bodies, checks owner/subscription/event authority, requires an adapter verifier, and stores a locator/hash for deduplicated targeted sync. Fixtures cover signature failures, conflicting replay, revocation, sanitized verifier errors, and dispatch outage reconciliation. The Google event tests cover the separate legacy ingress. No registered production universal adapter currently supplies `verify_event`; this is not live webhook acceptance. |
| Disconnect and learned-data deletion | `test_connection_management.py` | Tests cover source-bound commitment recomputation, person identity source support, event receipt/snapshot removal, and 409 responses for ambiguous legacy or partial provenance. Automatic deletion intentionally stops when it cannot prove ownership; it does not certify erasure of every historical or external provider copy. |
| Legacy Google watch retirement | `test_connection_management.py`, `test_intelligence_ingestion.py` | Calendar and Drive channel-scoped stops, disconnected-account checks, durable retries, and credential retention are fixture tested. Gmail/Calendar/Drive watches can retire after provider-confirmed expiry plus a five-minute grace period without OAuth or HTTP; provisional and pre-migration deadlines cannot authorize retirement. Tests cover malformed provider leases, registration/disconnect and cleanup/reconnect races, ownership, and deletion after retirement. A remote Google stop has not been verified here. |
| Browser-assisted contract | `test_connector_contracts.py`, `test_connector_runtime.py`, extension tests | A scoped one-shot HTTPS capture request is defined and ordinary sync denies browser-assisted adapters, including persisted consent flags. The extension offers explicit title/origin/note import through the managed JSON import path. A dedicated browser capture endpoint and registered adapter are not shipped. |

## Scope of the original completion gate

The original specification requests the **browser-assisted contract** in
implementation item 16. That contract and the explicit-action boundary are
implemented. A dedicated general browser adapter is further implementation,
not an additional mandatory completion condition for this release.

The Must list allows a live/mock unknown-service demonstration, but mandatory
demonstration D explicitly requests an **unknown live service**. Use the stricter
requirement for final acceptance. Demonstrations A/B/C do not explicitly require
live Google or institution-enabled Canvas accounts; their functional evidence
is the existing provider-fixture path through the runtime, SPEC-002 and Today.
Live provider suitability remains unverified. A new universal production webhook
adapter is not separately enumerated as a completion condition: Google retains
its existing authenticated ingress and targeted source workflows. Enabling any
additional event provider still requires its actual verifier and integration
evidence.

The numerical targets, including OAuth refresh success ≥99%, health accuracy
≥98%, incremental correctness ≥99%, event deduplication ≥99.9%, stable identity
≥99.9%, and replay duplication <1%, need explicit samples, numerators and
denominators. The specification does not label every metric a production rate.
Fixture/staged measurements must identify their scenarios and cannot be
presented as measured production rates. Test totals alone are not denominators
for each of these metrics.

Still required: record demonstration D using the live acceptance command in
`spec-003-portability-acceptance.md`; assemble the metric-specific evidence;
resolve or explicitly review lifecycle limitations for ambiguous historical
provenance and unconfirmed old watches; retain zero accepted SPEC-002 regressions
on the full API/hosted gates. Record the exact commit, environment, sample size,
numerator, denominator and failures. A live result from one service establishes
that interoperability case, not safe semantics for every REST or MCP endpoint.
