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
benchmark. It does not enable REST onboarding, MCP, unrestricted network access,
third-party code execution, or writes. Domain/DNS protections, generic-provider
setup, event subscription lifecycle, safe learned-data deletion, and the remaining
SPEC-003 acceptance requirements need their own implementation and validation.

A passing suite is evidence for these named behaviors only; it is not a declaration
that SPEC-003 is complete.
