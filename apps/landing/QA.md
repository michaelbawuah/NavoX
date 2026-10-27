# Landing QA evidence

## Passed

- All eight app PNGs, all fourteen individual Noa SVGs, and Three.js match the embedded PDF attachments byte for byte. The supplied OG image and vendor license are retained.
- Local static preview and Vercel production deployment load successfully. All static references resolve.
- Cloud Chrome: dark/light toggles, four accent controls, hero chat/Noa playback without WebGL, and truthful no-endpoint form submission.
- Responsive iframe checks at 390px and 360px outer widths: document width equals available viewport width (375px/345px with desktop scrollbars); no horizontal document overflow. These are desktop browser responsive checks.
- Nine Node form-response tests cover missing endpoint, invalid email, 200/201, 409, 429, 422/503, and offline responses. CI runs them and checks the source rebuild.
- Ruff lint/format and strict mypy pass across services/api.
- Twenty-six original waitlist, health and request-hardening tests pass independently with SQLite and PostgreSQL. Focused waitlist tests include persistent duplicates, database-backed rate limiting, spoofed-forwarding rejection, validation/error redaction, CORS and database failure.
- The standalone service adds two tests for public-route isolation and refusing bootstrap against the student database; all 28 targeted tests pass locally.
- Live HTTPS waitlist returns 201, 409, 422 and 429; a normalized PostgreSQL record, CORS, Retry-After and email-free logs were verified. The disposable test registration was removed. The form was connected only after these checks.
- Dependency audit passes after updating the existing filelock dependency to 4.2.1.
- Alembic renders the complete PostgreSQL schema including the new waitlist tables. Migration tests also check a real upgrade/downgrade roundtrip.

## Pending

- Main-API migration reconciliation with the concurrent milestone branch remains a future integration step. The public waitlist currently runs independently with a dedicated database.
- Apex DNS cutover and final app-domain authentication verification. App/API A records and valid HTTPS are configured.
- A WebGL-capable desktop browser: wave, meeting drop/bounce, hover tooltips, shadows, theme recoloring and drag.
- Real iPhone Safari with Reduce Motion off and on; real Android Chrome; desktop Safari and Firefox.
- Full carousel touch/drag, tilt/parallax, reward confetti and reduced-motion checks on the required devices.
- Lighthouse Accessibility >=95 / Best Practices >=95 and actual iMessage/Slack OG previews.

The available cloud browser does not create a WebGL context. The fallback runs without the Three.js context-creation error and keeps the chat, Noa and controls usable. Screenshots visibly reflect this limitation; no 3D render has been fabricated.
