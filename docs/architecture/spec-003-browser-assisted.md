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

The Chrome extension also offers an explicit page-note path using the existing
generic JSON import API. After the user opens NavoX and clicks Choose current
page, the side panel displays the title and site origin. The user writes a note
and clicks Save before the service worker requests import preview and confirmation.
Only the origin, title and note enter an encrypted owner-bound snapshot, then
the existing connector runtime and SPEC-002 ingestion. The extension never reads
page body content or browsing history; it removes URL path, query and fragment
before sending anything. A repeated identical note reuses the import digest.

This is an explicitly selected browser context import. It does not register a
`BROWSER_ASSISTED` adapter or unlock background browser sync. A dedicated
browser adapter with a trusted capture API and one-use authorization remains a
separate future integration; a caller cannot activate it by changing config.
