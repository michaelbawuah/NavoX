# Landing QA evidence

## Passed

- All eight app PNGs, all fourteen individual Noa SVGs, and Three.js match the embedded PDF attachments byte for byte. The supplied OG image and vendor license are retained.
- Local static preview and Vercel production deployment load successfully. All static references resolve.
- Cloud Chrome: dark/light toggles, four accent controls, hero chat/Noa playback without WebGL, and truthful no-endpoint form submission.
- Responsive iframe checks at 390px and 360px outer widths: document width equals available viewport width (375px/345px with desktop scrollbars); no horizontal document overflow. These are desktop browser responsive checks.
- Nine Node form-response tests cover missing endpoint, invalid email, 200/201, 409, 429, 422/503, and offline responses. CI runs them and checks the source rebuild.
- Ruff lint/format and strict mypy pass across services/api.
- Twenty-six waitlist, health and request-hardening tests pass independently with SQLite and PostgreSQL. Focused waitlist tests include persistent duplicates, database-backed rate limiting, spoofed-forwarding rejection, validation/error redaction, CORS and database failure.
- Alembic renders the complete PostgreSQL schema including the new waitlist tables. Migration tests also check a real upgrade/downgrade roundtrip.

## Pending

- API deployment, migration reconciliation with the concurrent milestone branch, and real HTTP checks on api.navox.net. The public form intentionally has no endpoint.
- IONOS DNS and app-domain authentication migration; navox.net still reaches the existing app.
- A WebGL-capable desktop browser: wave, meeting drop/bounce, hover tooltips, shadows, theme recoloring and drag.
- Real iPhone Safari with Reduce Motion off and on; real Android Chrome; desktop Safari and Firefox.
- Full carousel touch/drag, tilt/parallax, reward confetti and reduced-motion checks on the required devices.
- Lighthouse Accessibility >=95 / Best Practices >=95 and actual iMessage/Slack OG previews.

The available cloud browser does not create a WebGL context. The fallback runs without the Three.js context-creation error and keeps the chat, Noa and controls usable. Screenshots visibly reflect this limitation; no 3D render has been fabricated.
