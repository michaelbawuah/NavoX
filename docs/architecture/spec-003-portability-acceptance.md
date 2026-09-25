# SPEC-003: configured unknown-service acceptance

`tests/test_connector_portability.py` supplies a randomly named external provider
through the existing GenericAPIConnector configuration, then executes the real
ConnectorRuntime, canonical normalization, OperationalExtractor validation,
SPEC-002 resolution/persistence, and Today projection. Neither provider-specific
intelligence nor a hardcoded provider identity is added to the core engines.

## Covered assertions

The same suite runs with no API credential and with a synthetic bearer token
stored using the real owner-bound SecretBroker. It checks:

- A configured field mapping yields one actionable commitment and its provenance
  in Today, with the expected deadline.
- A fresh sync request for the identical revision creates no second commitment,
  evidence row, resource, or model extraction. Per-run acceptance receipts remain
  distinct and point to the same revision/result.
- Policy denial, wrong owner, wrong workspace, pause, and grant removal block both
  HTTP requests and model calls.
- Credential leases have closed before model processing; the synthetic API token
  does not enter SourceDocument, canonical registry content, or audit metadata.
- Model output containing an attempted permission grant is rejected and creates
  neither a commitment nor an action/approval, and leaves capabilities unchanged.

Run from `services/api`:

```bash
uv run pytest tests/test_connector_portability.py
```

Set `NAVOX_CONNECTOR_TEST_DSN` to a **disposable** PostgreSQL database to repeat the
suite using isolated schemas. The test owns and removes only its random schemas.
Without that setting, it uses temporary SQLite databases.

## What this does not certify

HTTP responses and model proposals are fixtures. This is the configured mock
unknown-service demonstration, not a live public endpoint or model-quality
benchmark. Separate code now supplies operator-reviewed REST configuration and
owner-consented setup, reviewed MCP read setup, bounded outbound requests,
authenticated event ingress for adapters that implement verification, and
owner-scoped disconnect/deletion. See `spec-003-generic-rest-onboarding.md`,
`spec-003-mcp-read.md`, and `spec-003-certification.md` for those distinct fixture
checks and their limits. This portability test does not exercise those setup,
webhook, or cleanup paths, nor does it authorize arbitrary domains, third-party
code execution, or writes. No shipped universal adapter currently implements
event verification. Live unknown-service ingestion, real provider behavior,
Google watch retirement and actual historical deletion still need deployment
evidence. Numerical fixture measurements now have their own explicit counters
and thresholds in `spec-003-certification.md`; they are not live reliability rates.

A passing suite is evidence for these named behaviors only; it is not a declaration
that SPEC-003 is complete.

## Live demonstration command

The operator command below uses an existing owner-authorized Generic REST or MCP
connection. Set up that connection using the reviewed deployment configuration
and Connected Apps flow first. Select a service containing an actionable item
that should appear in Today. The API and Temporal worker must run the same
reviewed revision and operator configuration, with a configured AI provider.
Record `git rev-parse HEAD` for that deployment alongside the resulting report.

List candidate connection IDs in the running app:

```bash
docker compose exec -T api uv run --no-sync python -m navox.evaluation.connector_live --list
```

Then run the chosen connection (replace the UUID):

```bash
docker compose exec -T api uv run --no-sync python -m navox.evaluation.connector_live \
  --connection-id 00000000-0000-0000-0000-000000000000 --live \
  > spec-003-live-portability.json
```

`--live` starts a real manual sync and can consume provider/model quota and add
the source's normal derived results to Today. It uses the deployed Temporal
workflow, production connector registry, existing grants and Secret Broker. It
does not create a connection or grant new permissions. The runner checks current
owner/membership, connection state and operator-approved manifest before dispatch
and again before collecting evidence. The default wait is 300 seconds; use
`--timeout-seconds` from 30 to 1800 if necessary. A timeout ends the wait only:
the durable sync may continue. Its request ID is included in the error result.

A passing result requires this new run to complete, observe resources, record
canonical acceptance receipts for the SPEC-002 consumer, and link at least one
of those receipts' results to Today with this connection's evidence. Old Today
cards alone cannot pass the gate. An informational-only source or an empty
incremental page will fail the demonstration without fabricating a task; use a
new connection or a real new actionable item for the demonstration.

The JSON contains identifiers, a manifest digest, counts and individual checks.
It omits titles, bodies, URLs, credentials and provider error text. Exit status
is nonzero if execution fails or any gate fails. This single run does not claim
the numerical reliability targets or production-wide security certification.
Tests of the command use provider/model fixtures and are not live acceptance
results themselves. Keep the report and deployment revision together when
recording demonstration D.

## Prepared live service: this repository's GitHub issues

`examples/connectors/navox-github-issues.json` is a ready-to-review configuration
for the actual `michaelbawuah/NavoX` repository. It maps GitHub's `id`, `title`,
`body`, `updated_at`, `html_url` and `state` fields through the existing generic
adapter. There is no GitHub-specific intelligence or new core adapter. The repo
is private, so this example requires an owner-provided token. Do not paste it
into JSON, `.env`, reports, a terminal command or chat; enter it in Connected Apps.

The [GitHub repository issues documentation](https://docs.github.com/en/rest/issues/issues#list-repository-issues)
requires repository **Issues: read** permission for a fine-grained token. Scope it
to this repository. The endpoint also returns pull requests; this example reads
only the ten most recently updated open records and does not follow GitHub's Link
pagination headers. It is a bounded interoperability demonstration, not a complete
repository mirror. The owner supplied a passing deployed-worker report for this
service on 2026-09-25, recorded below.

1. Check out `spec-003-universal-connectors` in your local NavoX checkout and pull
   its latest revision. Preserve any local changes before switching branches.
2. Print the configuration as an environment value:

   ```bash
   python3 - <<'PY'
   import json
   from pathlib import Path
   entry = json.loads(Path("examples/connectors/navox-github-issues.json").read_text())
   print("GENERIC_REST_CONNECTORS=" + json.dumps([entry], separators=(",", ":")))
   PY
   ```

   Put that line in `.env`. If `GENERIC_REST_CONNECTORS` already contains approved
   services, append the new object to its existing JSON array instead of replacing
   them. API and worker both read `.env` in the existing Compose configuration.
3. Run `docker compose up -d --build api worker`. The migration service applies
   the current schema. Keep your existing AI provider configuration.
4. In **Connected Apps → Generic REST API**, choose **NavoX GitHub issues**,
   approve `development.issues.read`, and enter the repository-scoped token.
5. Run the `--list` and `--connection-id ... --live` commands above. The connection
   can already have synced: a fresh run may reuse identical accepted revisions,
   but it must still read the live source and link that run's receipts to Today.

Use a real open task assigned to you with a clear requested action; an existing
issue or PR body must actually contain actionable evidence. If the report says
`source_linked_results_in_today: false`, inspect the source and Today rather than
creating a fabricated result or overriding extraction. Record the deployment
commit and JSON report to close mandatory demonstration D.

## Recorded owner-run live acceptance — 2026-09-25

The owner supplied the deployed report generated at `2026-09-25T11:40:49.433700+00:00`
on commit `e372494a1aa0f4c626aa57789c5930ecd077927a`. It reports `passed: true`
through `deployed_temporal_worker` for generic REST connection
`8c7ac295-9734-5f0c-ab54-026ec3881a8b`, sync run
`3b0282a2-bce0-49f7-849c-eaaa79afa175`, and manifest SHA-256
`d420ae5f251d72332a5fb8cf31248f7e41821caeb920d70606a19e766b699123`.

All five checks passed: sync completion, resources seen, canonical acceptance,
SPEC-002 results, and source-linked Today results. The run saw two live resources,
accepted two receipts, and linked four result IDs/four Today matches. It reused
two existing revisions and processed zero new revisions; this report does not
claim four new tasks were created during the verification run. This is owner
supplied live evidence for mandatory demonstration D, not an independently
queried database result or a production reliability-rate measurement.
