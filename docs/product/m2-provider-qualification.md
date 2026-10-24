# M2 provider setup and live qualification

Qualification origin: **https://vox-test.66-175-235-169.nip.io**. Updated 2026-10-24.

The test operator reports that Google client ID/secret and Microsoft credentials
are absent. Google and Microsoft live consent, mailbox access and sending remain
**pending qualification**. Existing push configuration and worker activity do not
establish these provider capabilities. Unit tests, mocked provider responses and
an available Connections screen are separate evidence from a real provider run.

## Deployment configuration

Use the existing private environment file referenced by `NAVOX_VOX_ENV_FILE`.
Keep credentials in the deployment secret store. This document contains setting
names and public URLs only; do not append secrets, authorization codes, refresh
tokens, private mailbox content or a real user's address to qualification logs.

| Setting | Required test value or source |
| --- | --- |
| `NAVOX_PUBLIC_ORIGIN`, `WEB_ORIGIN` | `https://vox-test.66-175-235-169.nip.io` |
| `GOOGLE_OAUTH_CLIENT_ID` | Operator's Google web OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Matching client secret, supplied privately |
| `GOOGLE_OAUTH_REDIRECT_URI` | `https://vox-test.66-175-235-169.nip.io/api/v1/connections/google/callback` |
| `GOOGLE_TOKEN_ENCRYPTION_KEY` | Existing valid Fernet key used by the Google credential vault |
| `MICROSOFT_OAUTH_CLIENT_ID` | Operator's Microsoft application/client ID |
| `MICROSOFT_OAUTH_CLIENT_SECRET` | Matching client secret, supplied privately |
| `MICROSOFT_OAUTH_TENANT` | `common` for the intended school and personal account qualification |
| `MICROSOFT_OAUTH_REDIRECT_URI` | `https://vox-test.66-175-235-169.nip.io/api/v1/outlook/callback` |
| `CONNECTOR_SECRET_ENCRYPTION_KEY` | Existing valid Fernet key; Microsoft secret/draft storage also supports the existing `GOOGLE_TOKEN_ENCRYPTION_KEY` fallback |

Preserve encryption keys already protecting stored credentials, reply reviews
and Vox checkpoints. Configuring OAuth is not a reason to replace a key. Google
specifically requires `GOOGLE_TOKEN_ENCRYPTION_KEY`; the connector-key fallback
does not replace that Google vault requirement. Do not place secrets in
`NEXT_PUBLIC_*` variables.

Deploy matching web/API code and apply migration `0060_outlook_mail_drafts` before
testing Outlook. The isolated compose stack pairs
`NAVOX_TEST_CALENDAR_AVAILABILITY_ENABLED` with the API's
`STUDENT_CALENDAR_AVAILABILITY_ENABLED` and web build's
`NEXT_PUBLIC_NAVOX_STUDENT_CALENDAR_AVAILABILITY_ENABLED`. Rebuild the web bundle
when public build flags change. Calendar writes remain a separate release gate.
See [settings](../../services/api/navox/core/settings.py),
[credential vault](../../services/api/navox/core/credential_vault.py),
[secret broker](../../services/api/navox/connectors/secrets.py) and
[test compose](../../docker-compose.vox-test.yml).

## Register the two applications

**Google:** enable the Gmail API and Google Calendar API in the selected project;
configure the OAuth consent audience for the intended test accounts. Create a
web application OAuth client and register the exact Google callback above as an
authorized redirect URI. Supply its credentials privately. Provider verification
and organization restrictions must be resolved for the intended audience before
claiming wider availability. Follow Google's
[web-server OAuth setup](https://developers.google.com/identity/protocols/oauth2/web-server).

**Microsoft:** register an application supporting organizational directories and
personal Microsoft accounts, and add the exact Microsoft callback above under
the **Web** platform. Configure delegated Graph permissions for `User.Read`,
`Mail.Read` and the separately requested `Mail.Send`; the shipped flow uses a
client secret and authorization code with PKCE. Qualify one school/work mailbox
and one personal Outlook mailbox separately. Record any school administrator
consent restriction as an unmet gate. The implementation accepts tenant values
`common`, `organizations`, `consumers` or a tenant GUID. See Microsoft's
[registration guide](https://learn.microsoft.com/en-us/graph/auth-register-app-v2)
and [permission model](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc).

## Exact consent boundaries

All route paths below start with `/api/v1`. Use the NavoX UI with its current
browser session. Outlook and student calendar operations also bind requests to
`X-NavoX-Account: <workspace UUID>:<user UUID>`; writes validate the configured
Origin. Outlook and student-calendar callbacks must complete in the initiating
account/session.

| Purpose | Start route | Requested scopes |
| --- | --- | --- |
| Google identity | `GET /connections/google/start` | `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/userinfo.profile` |
| Existing Google read integration | `POST /connections/google/{connection_id}/intelligence/start` | `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/calendar.events.readonly` |
| Google availability purpose | `POST /student/calendars/google/start` | `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/calendar.calendarlist.readonly`, `https://www.googleapis.com/auth/calendar.events.freebusy` |
| Gmail send upgrade | `GET /connections/google/{connection_id}/gmail-send/start` | `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/gmail.send` |
| Outlook read | `POST /outlook/connect`, `confirmed:true`, `send:false` | `openid`, `profile`, `email`, `offline_access`, `User.Read`, `Mail.Read` |
| Outlook send upgrade | Same route, existing `connection_id`, `confirmed:true`, `send:true` | Outlook read scopes plus `Mail.Send` |

The existing Google read flow bundles Gmail and Calendar event reading. The
student availability grant is separately recorded with consent version
`calendar-availability.v1`. Neither availability nor mailbox read consent
approves sending or calendar creation. Outlook email connection does not enable
Outlook calendar availability. Calendar creation has separate consent and flags
described in [the calendar write contract](calendar-write-contract.md).

Source: [Google routes](../../services/api/navox/api/connections.py),
[calendar OAuth](../../services/api/navox/student_calendars/oauth.py),
[calendar scopes](../../services/api/navox/student_calendars/schemas.py),
[Outlook routes](../../services/api/navox/api/outlook.py) and
[Microsoft adapter](../../services/api/navox/providers/microsoft_mail.py).

## Live acceptance gates

Use controlled test accounts and an explicitly approved test recipient. Each
real send still requires review of the exact recipient, subject and full body
and an explicit Send action. Record build/commit, timestamp, provider/account
type, granted scope names, outcome and redacted receipt evidence.

| Gate | Required live evidence |
| --- | --- |
| Setup and callbacks | Both callbacks return to the correct NavoX owner; canceled/expired consent shows a safe failure; missing configuration stays visibly pending. |
| Read without send | Google and both Microsoft account types show the correct mailbox and readable controlled source; read-only accounts cannot send. Microsoft native `CONNECTED`/`DEGRADED` status is combined with actual `read_enabled`/`send_enabled` permissions. |
| Availability | Saved routine loads with per-day wake times, travel and alarm fields. A controlled Google busy interval conflicts with the proposed time; classes and protected routine time also block it. Missing, revoked or stale coverage remains unconfirmed. Reading an email never creates a meeting. |
| Review and approval | Drafting/shortening saves or suggests text without sending. A changed recipient/source or expired review requires another review. The final request binds the displayed revision and payload hash. |
| Send and recovery | A single approved reply reaches the intended controlled mailbox. Provider receipt verification establishes NavoX's sent state. A lost response remains unconfirmed; Check outcome performs receipt lookup and never resends. Reopening the source cannot create a second reply while a send record exists. |
| Account and privacy | Switching owner suppresses late private responses. Pause/disconnect stops new provider work; revoked access is visible. Disconnect/retention cleanup clears saved reply content while preserving content-free unresolved/completed receipt records. Reconnect the same mailbox to check an unresolved receipt. |

### Outlook coverage and outcome limits

Outlook checks **one page of at most 50 recent Inbox messages**, ordered by
received time. It surfaces possible requests and follow-up flags, skips
unsupported sender formats, and reports when more mail exists. It does not
follow the provider's next-page URL. Pending and completed source messages move
out of Needs reply; their records remain in Waiting. This is a bounded candidate
scan refreshed while the UI is active, not a claim of general ongoing triage or
later-reply monitoring.

Graph `202 Accepted` is not a verified sent result. Success requires the exact
message identifier and matching content fingerprint in Outlook Sent, followed
by `status:"completed"` and a non-null `verified_at`. This proves the matching
Sent record, not recipient delivery. Missing or ambiguous receipts remain
`executing`/`uncertain`; there is no automatic send retry. The saved review's
approval window is 15 minutes; private reply content becomes unavailable after
7 days. Draft/list access clears expired stored payloads, and disconnect clears
them immediately. Read-only receipt reconciliation can continue after reconnect
using the retained digest. See
[Outlook service](../../services/api/navox/outlook/service.py) and
[Microsoft adapter](../../services/api/navox/providers/microsoft_mail.py).

Mark these gates complete only after recording the live evidence. Credentials
being configured, a successful build, or a mocked send test does not close them.
