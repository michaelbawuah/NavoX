# SPEC-003 browser-assisted capture boundary

The connector manifest permits `BROWSER_ASSISTED`, but ordinary sync is
intentionally denied for this class. A stored `explicit_capture_authorized`
flag or a caller-supplied `browser_capture` trigger is not proof of a fresh user
action. This blocks background polling and scheduled jobs from collecting
browsing context through the universal sync runtime.

`BrowserCaptureRequest` defines the separate one-shot adapter input: a specific
connection, workspace, user, request ID, HTTPS page URL without embedded
credentials, and a timezone-aware authorization time. `BrowserAssistedConnector`
exposes `capture` separately from `sync`. A future trusted API entry point must
derive identity from the authenticated session, verify a visible user gesture or
an active and clearly visible tracking authorization, validate the page origin
against the connector's declared scope, bind a short-lived one-use request ID,
and pass only selected page context to the adapter. The adapter must produce
canonical resources, and the runtime must apply the normal capability,
provenance, tenant, size and content checks before acceptance. No page data or
browser history should enter a background cursor.

This commit provides the contract and the fail-closed ordinary-sync boundary.
There is no browser capture API, browser extension capture command, or registered
browser adapter. A caller cannot activate capture by changing connection config;
the explicit-consent capture flow needs its own implementation and acceptance
tests before this connector class can ingest data.
