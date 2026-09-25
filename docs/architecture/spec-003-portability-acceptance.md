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
Google watch retirement, and numerical reliability/security targets still need
independent acceptance evidence.

A passing suite is evidence for these named behaviors only; it is not a declaration
that SPEC-003 is complete.
