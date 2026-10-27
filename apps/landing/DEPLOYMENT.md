# Landing and app domain cutover

Vercel project: `navox-landing` (`prj_JBmdtF6mLTLDHqwXyoz87E9IYSpY`). Static root: `apps/landing/site`; no build/install command; output `.`. The deployed landing is https://navox-landing.vercel.app/.

The current production app still serves **navox.net** through the VPS Caddy proxy. Neither `app.navox.net` nor `api.navox.net` has a DNS record. The API's configured WEB_ORIGIN and Google/Canvas OAuth callbacks still use navox.net. Do not move the apex until app-domain authentication works.

## Domain records (IONOS)

| Name | Type | Value | Order |
| --- | --- | --- | --- |
| app | A | 66.175.235.169 | First: prepare app TLS and authentication |
| api | A | 66.175.235.169 | Prepare the waitlist service and TLS |
| @ | A | 76.76.21.21 | Last: switch the apex to Vercel |
| www | CNAME | cname.vercel-dns.com | Vercel redirects www to navox.net |

These are Vercel's current recommended records for this project. Preserve mail, verification, and other unrelated records. Check and reconcile any stale AAAA records for the same web hosts before cutover. Do not change nameservers.

## Release sequence

1. Add app/api DNS and append their Caddy host blocks after reviewing the live config. The existing app uses web :3000 and API :8000; preserve the assistant/student-voice/student-agent routes that go through web. Preserve the independent Vox test hosts.
2. Move API WEB_ORIGIN to https://app.navox.net and update the app's public origin where configured. Register the app-domain Google and Canvas callback URLs with their providers before changing runtime callback URLs. Verify sign-in, sessions, Google connection/callback and the app's current phone flows on app.navox.net. Retain a rollback of the old runtime and Caddy config. A DNS change alone does not complete this migration.
3. Reconcile this branch's `0053_landing_waitlist` migration with the other chat's application branch before merging/deploying the API. At inspection, PR #59 already has a separate 0053–0065 chain. Reparent the waitlist migration onto the merged application head (or use an explicit Alembic merge revision), then rerun migration tests and ensure exactly one head. Never apply one branch's Alembic head blindly to production.
4. Apply the reviewed waitlist migration and release services/api with `/v1/waitlist` routed on api.navox.net. Limit this public host to that endpoint. Configure the server to accept proxy headers only from the actual trusted reverse proxy; a Docker bridge must not collapse all callers to one shared IP, and arbitrary clients must not be allowed to forge it. Verify 201, 409, 422 and 429 against a disposable test address and confirm email-free logs. Remove the test registration through an operator-approved database operation.
5. Add `data-endpoint="https://api.navox.net/v1/waitlist"` to the source form and rebuild. Deploy the updated static folder. Do not enable the form before the API is live and verified.
6. Switch @/www to Vercel, then verify TLS, www redirect, app sign-in, live waitlist, security headers and share metadata. Confirm both app and landing rollbacks remain available.
7. Complete real-device/browser QA in the original guide. Cloud screenshots are responsive previews with WebGL unavailable, not phone testing. Do not claim Lighthouse scores or social share previews were verified without running those checks.

No waitlist email sending is implemented. Add verified unsubscribe and deletion paths before sending any mail.
