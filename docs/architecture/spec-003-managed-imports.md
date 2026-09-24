# SPEC-003: managed immutable file imports

This checkpoint enables the File imports catalogue entry and its preview/confirmation flow. It is not complete SPEC-003 certification.

## User flow

Browse connectors → Import a file → Preview without AI → review record count, mapped fields, time zone and examples → explicit confirmation → encrypted snapshot → identifiers-only Temporal dispatch → common Connector Runtime → existing SPEC-002 extraction/resolution → Today. Selecting a file alone does not upload it. Preview uploads it for bounded parsing but persists nothing and calls no model. Confirmation authorizes mapped source content to the configured model, not tool execution or write capabilities.

Limits: UTF-8 text, 2 MB per file, 1,000 records, 20 immutable snapshots per workspace owner. These are file snapshots, not live subscriptions. Existing snapshot content is not replaced. Identical source text/format/time zone for the same owner reuses the existing connection. Modified files are separate snapshots; this is not cross-snapshot semantic deduplication or deletion of old knowledge.

## Supported formats

CSV and JSON accept title/subject/name, optional id/uid/external_id, description/content/body/notes, ISO due_at/start_at/end_at, status, and source_url/url. Other fields are not passed into source documents. Duplicate IDs, duplicate JSON keys, mismatched CSV rows, invalid numbers, unsafe links and oversized records are rejected rather than silently truncated. Without an explicit ID, normalized content supplies a stable digest; indistinguishable duplicate records must have distinct IDs.

ICS supports individual VEVENTs with UID, SUMMARY and DTSTART; UTC, IANA TZID, floating local times interpreted in the previewed user time zone, all-day dates, DTEND and cancellation. DATE values remain dates and DTEND is exclusive. Ambiguous/nonexistent local times are rejected rather than guessed. Recurrence, DURATION, VTODO, custom VTIMEZONE rules and recurrence exceptions are explicitly unsupported in this checkpoint; export individual events with IANA/UTC times. Nested alarms and attachments are never executed or fetched. The parser is a documented subset, not a claim of full RFC 5545 implementation. Reference: https://www.rfc-editor.org/rfc/rfc5545

## Privacy and authorization

The original file is transient. Only mapped normalized records are retained, encrypted in connector_import_snapshots, separate from credential storage and runtime history. The encrypted envelope binds connection, workspace, owner, digest and a fixed import timestamp. Connection config contains format/digest/owner identifiers only; workflow payloads contain identifiers only; runtime registry persistence is locator-only. Normal source evidence remains under existing SPEC-002 controls. No new provider credentials or write grants are introduced.

Cookie-origin checks, JSON content-type and streaming request-size bounds precede parsing. Validation responses omit Pydantic input echoes. Creation is owner-scoped and serialized; a request ID cannot create different snapshots. Snapshot reads reload current membership and connection authority; the common runtime rechecks its lease, generation and authorization before accepting facts. Pause/resume retains the snapshot and the existing ownership fence. A dispatch outage returns an explicit pending result after durable save; scheduled reconciliation can recover an unprocessed snapshot.

Import encryption uses CONNECTOR_SECRET_ENCRYPTION_KEY, falling back to the configured Google encryption key. No key rotation or safe delete-learned-data implementation is claimed here. Those require the separate lifecycle/privacy work remaining in SPEC-003.

## Verification

Run the complete existing API and web gates. Repeat test_managed_imports.py on disposable PostgreSQL via NAVOX_CONNECTOR_TEST_DSN. Tests use actual parser/storage/runtime/resolution/Today code; only external model output and dispatch are fixtures. They do not establish live-model accuracy. Check upgrade → downgrade to 0016 → upgrade, including table/index/definition presence. Full hosted CI is required on the exact published head. An isolated candidate is not a published feature checkpoint.
