# Vox accounts and secret handoff

No real credential belongs in this repository, chat, screenshots, logs or a public browser bundle. Existing server credentials can be reused after presence/permission checks; do not create duplicate OAuth applications unnecessarily.

## Needed for milestone 1

- LiveKit Cloud project: project WSS URL, API key and API secret. Backend settings are STUDENT_VOX_LIVEKIT_URL, STUDENT_VOX_LIVEKIT_API_KEY, STUDENT_VOX_LIVEKIT_API_SECRET; the agent registration uses the same project. The browser receives a short-lived room-specific participant token, never the project secret. Separate development and production projects.
- OpenAI funded API project: project/service-account OPENAI_API_KEY with Model capabilities Request for realtime, transcription, text and speech requests. Models Read is useful for checking availability. No organization-admin or Files permission is needed for milestone 1. Current primary model gpt-realtime-2.1; initial stock voice options marin and cedar, subject to actual listening tests.
- Local wake: Picovoice Porcupine Web evaluation/production agreement, custom Hey NavoX model for Web (WASM), language .pv and custom .ppn assets. Commercial terms and browser-delivered AccessKey restrictions must be confirmed before public release. The runtime token cannot be represented as an unextractable server secret when the WASM SDK itself needs it. Picovoice currently says AccessKeys cannot be reset through Console and should not be shared; obtain its explicit Web deployment/credential-handling agreement rather than assuming a public-key or domain-restriction feature. Both STUDENT_VOX_LOCAL_WAKE_ENABLED and STUDENT_VOX_PORCUPINE_BROWSER_CREDENTIAL_APPROVED default false and must remain false until that agreement and approved assets exist. No fallback to cloud speech recognition.

A private empty credential template was prepared on Michael's Mac at /Users/mba_steins/NavoX-private-backups/vox-m1-secrets.env with mode0600. It contains blank LiveKit, OpenAI and Porcupine credential fields. Fill it locally, supplying the wake credential only after the vendor approves the Web deployment. Tell the builder only that it is ready; never send values in chat. It is outside all Git worktrees and is not automatically sourced into production.

## Needed for later milestones

- Google Cloud OAuth web client: client ID and secret, Calendar API and Gmail API, External consent setup/test users and production verification when applicable. Existing callback: https://navox.net/api/v1/connections/google/callback; add the exact isolated preview callback only after its hostname exists. Existing calendar scopes: openid, userinfo.email, calendar.calendarlist.readonly, calendar.events.freebusy; separately consent calendar.events.owned for writes to owned calendars. Gmail uses gmail.readonly and separately gmail.send when sending is enabled. No all-mail/permanent-delete scope. If shared-calendar writing is later required, review the narrower existing owned-calendar limit explicitly.
- Microsoft Entra OAuth web app: client ID, supported tenant/account configuration and secret or certificate. Delegated Calendars.ReadWrite plus openid/profile/offline_access; school tenant policy can still require administrator consent. No tenant-wide application calendar permission. Planned callback /api/v1/connections/microsoft/callback must be implemented and the preview URL confirmed in milestone3.
- Apple/iCloud web test account: Apple ID with2FA and app-specific password, entered privately through its connection flow. Do not use the normal Apple ID password. Native EventKit is a later native part, not a required developer account for web CalDAV.
- Internal encryption keys are generated/stored server-side; no external account is required. Reuse the deployed vault key only through secret storage, never by copying it into chat or tracked examples.

## Runtime configuration and cost reports

STUDENT_VOX_INFERENCE_CONFIG_PATH selects one operator-owned JSON configuration, reloaded for new calls. Models, compatible API endpoints, credential references and voices remain separate settings. Credentials stay in environment/secret storage. API-incompatible future providers require an adapter; an OpenAI-compatible self-hosted service can use the same protocol without a Vox UI rebuild.

The allowlist STUDENT_VOX_OPERATOR_USER_IDS defaults empty. Only explicitly configured operator account IDs can read GET /api/v1/vox/usage/daily and GET/PUT /api/v1/vox/usage/policy. Students can read only their own status/call totals. The policy is persisted in PostgreSQL and changes without redeploying. A policy example records the approved initial quotas and dated primary-model price rates. LiveKit transport rates must match the selected actual plan; unset rates mean unknown, not free.

Unknown provider usage or incomplete pricing is visible. Estimates are distinct from reconciled invoice amounts. Connected transport minutes can include silence even though device audio upload is stopped. Physical-phone testing, true session rollover, fallback behavior and real cost reconciliation remain unaccepted until observed.

## Official setup references checked 2026-10-23

- https://docs.livekit.io/agents/models/realtime/openai/
- https://docs.livekit.io/frontends/reference/tokens-grants/
- https://developers.openai.com/api/docs/guides/realtime
- https://developers.openai.com/api/docs/guides/realtime-conversations
- https://developers.openai.com/api/docs/guides/rbac
- https://developers.openai.com/api/docs/pricing
- https://picovoice.ai/docs/quick-start/porcupine-web/
- https://picovoice.ai/docs/faq/general/
- https://developers.google.com/workspace/calendar/api/auth
- https://developers.google.com/workspace/gmail/api/auth/scopes
- https://learn.microsoft.com/en-us/graph/permissions-reference
