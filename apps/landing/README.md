# NavoX landing

Static marketing site extracted from the four supplied October 2026 PDFs. Noa SVGs and PNGs are preserved. Three.js r128 and its MIT license are vendored.

## Run and rebuild

```sh
cd apps/landing/site
python3 -m http.server 8080
```

Edit `source/src.html`, then run from repository root:

```sh
node apps/landing/source/build.js apps/landing/site
```

The deployment itself needs no build command. Vercel project root: `apps/landing/site`, framework Other, output `.`. The app remains separate on `app.navox.net`. `www.navox.net` redirects to `navox.net`. Cache headers permit revalidation so unchanged filenames do not trap old releases.

## Waitlist

`POST /v1/waitlist` accepts `{ "email": "student@example.edu", "source": "landing" }`. EmailStr validation, lowercase normalization, a primary-key unique index, and shared database counters enforce 201 / 409 / 422 / 429. The limit is five requests per IP per fixed UTC minute; duplicate and invalid-email attempts count. Counters expire after two minutes. Responses and application logs never echo emails or database parameters. Run behind a trusted proxy that sets the ASGI client address; never expose the service directly or trust arbitrary forwarding headers.

The isolated public subapp allows only `https://navox.net`, plus `http://localhost:8080` outside production. It does not change the application's existing authenticated CORS policy. Use `navox.api.waitlist_service` with the dedicated `navox_waitlist` database for an independent release; bootstrap with `python -m navox.api.waitlist_service`. It refuses the student database. The main-API migration `0053_landing_waitlist` must be reconciled with concurrent application migrations before a combined release.

Keep the form without `data-endpoint` until a deployed API has passed its HTTP checks. Its initial and submitted message remains “Early-access sign-ups open soon. Check back here.” Then set `data-endpoint="https://api.navox.net/v1/waitlist"` on `#joinForm` in the source and rebuild.

No email delivery is implemented. Add verified unsubscribe and deletion paths before sending any waitlist mail.

## QA status

API validation: run Ruff, Ruff format, strict mypy, and `pytest tests/test_waitlist.py tests/test_health.py tests/test_hardening.py`.

Cloud Chrome supports UI verification but has no WebGL context. Verify its graceful fallback independently of the 3D animation. Real iPhone Safari (Reduce Motion off/on), real Android Chrome, desktop Safari/Firefox, WebGL-capable Chrome, Lighthouse and actual iMessage/Slack share previews remain manual release checks until those devices/tools are available. Responsive previews are not evidence of real device testing.

Content follows the supplied guide: no testimonials, user counts, school logos, or secrets; all colleges; original Noa unchanged.
