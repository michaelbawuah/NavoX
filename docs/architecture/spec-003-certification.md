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
| Legacy Google watch retirement | `test_connection_management.py` | Calendar and Drive channel-scoped stops, disconnected-account checks, durable retries, and credential retention are fixture tested. Gmail mailbox-wide stop is intentionally disabled; its watch remains pending until verified expiry or coordinated manual cleanup. A remote Google stop has not been verified here. |
| Browser-assisted contract | `test_connector_contracts.py`, `test_connector_runtime.py` | A scoped one-shot HTTPS capture request is defined and ordinary sync denies browser-assisted adapters, including persisted consent flags. The user gesture, capture endpoint and browser integration are not shipped. |

The numerical SPEC-003 targets, including OAuth refresh success ≥99%, health
accuracy ≥98%, incremental correctness ≥99%, event deduplication ≥99.9%,
stable identity ≥99.9%, and replay duplication <1%, require a representative
denominator and measured production or staged provider runs. A passing fixture
suite establishes correctness for its named cases; it is not a statistical
estimate of these rates. The completion gate also requires live authorized
Google and Canvas checks, a live unknown REST or MCP service, safe lifecycle
verification (including real Google watch retirement and a registered provider
event verifier), and confirmation that SPEC-002 regressions remain zero on the
full API/hosted gates. Reviewed REST/MCP configuration and owner consent have
fixture-backed setup paths; the tests do not establish provider interoperability
or safe semantics for a real MCP tool. Record live results with the exact commit,
environment, sample size, numerator, denominator, and failure examples before
declaring M8 complete.
