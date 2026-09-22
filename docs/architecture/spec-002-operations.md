# SPEC-002 operation and verification

## Enable locally

1. Pull main, preserve your existing `.env`, then rebuild with
   `docker compose up --build -d`. Migration 0011 runs before API/worker startup.
2. Configure `AI_PROVIDER=openai`, `OPENAI_API_KEY`, and `OPENAI_MODEL` in the
   private environment. Use a model your account supports for strict Responses
   JSON schema. Restart/recreate both API and worker after changes. Keys are never
   entered in the web app or committed to Git.
3. Sign in and use **Enable Gmail & Calendar understanding** on the existing Google
   connection. Consent grants only Gmail readonly and Calendar events readonly.
   Enable both APIs and add the read scopes to the Google OAuth consent screen.
   Identity-only connections do not read messages or events.
4. Use **Sync Gmail** and **Sync Calendar**. Initial Gmail processing covers the
   last 30 days; Calendar covers primary-calendar events from 30 days ago through
   90 days ahead. Source changes subsequently use provider history/sync cursors.
5. Today shows evidence references, reasons, uncertainty and feedback controls.
   A sent request can become waiting; only evidence of the intended outcome
   completes it. An expired deadline alone never completes a task.

## Provider delivery and recovery

Gmail push requires `GOOGLE_GMAIL_WATCH_TOPIC` plus existing Pub/Sub subscription,
OIDC audience/service-account and verification-token settings. Calendar push uses
`GOOGLE_CALENDAR_PUSH_URL` ending in `/api/v1/events/calendar` on public HTTPS.
The worker creates/renews provider watches and stores lease expiry. Notification
handlers authenticate before creating a durable inbox row. A failed immediate
Temporal dispatch leaves that inbox row available to reconciliation.

Without public push delivery, the worker performs delta reconciliation after
30 minutes of cursor inactivity. It never performs repeated full-mailbox LLM
rescans. Expired provider cursors trigger a bounded initial scan plus revalidation
of previously referenced resources. A batch limit or model/provider failure leaves
the previous cursor unchanged. Check audit events for failure class and latency;
raw source text and provider error payloads are excluded.

The intelligence workflow contains identifiers only. PostgreSQL holds current
facts; Temporal retries work and refreshes time-dependent state and attention.
Pause and revoked scopes are checked again inside processing activities.

## Display context

Workspace settings persist IANA timezone, 12/24-hour clock, temperature unit and
optional weather city. Weather is opt-in, uses city-level Open-Meteo data, and has
no device geolocation or intelligence/notification side effects. Disabling weather
forgets the saved city. The UI displays attribution and gracefully handles outages.

## Evaluations and limits

`uv run pytest` runs application and security regressions. CI additionally runs
`tests/test_intelligence_*.py` into JUnit and produces the SPEC-002 regression
report. These fixtures exercise real application code with synthetic source/model
responses. They establish deterministic behavior, not live model accuracy.
The spec's precision/recall targets remain unmeasured until a representative,
independently labeled corpus is run through the configured live model.

Temporal/PostgreSQL deployment is validated by the repository Compose CI gate.
Real Google delivery, live extraction quality/cost, and mailbox demos require the
owner's private provider configuration. No private credentials are used by CI.

## Verified upstream contracts

- [Gmail incremental synchronization](https://developers.google.com/workspace/gmail/api/guides/sync)
- [Calendar incremental synchronization](https://developers.google.com/workspace/calendar/api/guides/sync)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Temporal retries](https://docs.temporal.io/develop/python/best-practices/error-handling)
- [Temporal history rollover](https://docs.temporal.io/develop/python/workflows/continue-as-new)
- [Open-Meteo forecast](https://open-meteo.com/en/docs) and [city search](https://open-meteo.com/en/docs/geocoding-api)

No dependency version changes were needed for this implementation.
