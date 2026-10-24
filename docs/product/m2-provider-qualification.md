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

Deploy matching web/API code and apply migrations `0060_outlook_mail_drafts` and
`0061_outlook_reply_tracking` before testing the current Outlook flow. The
second migration adds reply evidence separately from the durable send receipt;
existing receipts begin with later replies unchecked. The isolated compose stack pairs
`NAVOX_TEST_CALENDAR_AVAILABILITY_ENABLED` with the API's
`STUDENT_CALENDAR_AVAILABILITY_ENABLED` and web build's
`NEXT_PUBLIC_NAVOX_STUDENT_CALENDAR_AVAILABILITY_ENABLED`. Rebuild the web bundle
when public build flags change. The selected-message task flow similarly pairs
`NAVOX_TEST_EMAIL_ENABLED` with `STUDENT_EMAIL_ENABLED` and
`NEXT_PUBLIC_NAVOX_STUDENT_EMAIL_ENABLED`; saving the reviewed task also requires
the paired student planning flags. Calendar writes remain a separate release gate.
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

For Microsoft token responses, an absent `scope` field inherits only the
verified authorization attempt's requested scopes during code exchange, or the
already stored grant during refresh. An explicit empty or invalid field does
not use that fallback; explicit returned scopes still intersect the reviewed or
stored grant. This does not infer send permission from an unrelated login.
Microsoft documents the code-exchange omission in its
[successful response contract](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow#successful-response);
the unchanged-scope rules also appear in OAuth 2.0
[section 5.1](https://www.rfc-editor.org/rfc/rfc6749#section-5.1) and
[section 6](https://www.rfc-editor.org/rfc/rfc6749#section-6).

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

| Gate | Required live evidence | Status |
| --- | --- | --- |
| Setup and callbacks | Both callbacks return to the correct NavoX owner; canceled/expired consent shows a safe failure; missing configuration stays visibly pending. | Pending Google and Microsoft credentials and live consent. |
| Read without send | Google and both Microsoft account types show the correct mailbox and readable controlled source; read-only accounts cannot send. Microsoft native `CONNECTED`/`DEGRADED` status is combined with actual `read_enabled`/`send_enabled` permissions. | Pending live mailbox reads. |
| Source-linked meeting review | A selected controlled Gmail/Outlook message supplies its actual passage and sent date; ambiguous dates, time zones, quoted changes and multiple options require correction. Source/account changes retire the prior review. | Pending live-source qualification; deterministic interpretation fixtures are separate evidence. |
| Availability | Saved routine loads with per-day wake times, travel and alarm fields. A controlled Google busy interval conflicts with the reviewed time; classes and protected routine time also block it. Missing, revoked or stale coverage remains unconfirmed. | Pending live Google availability. |
| Selected email to task | Review one exact provider message, confirm the task title, course/deadline decision and effort, then save once. Confirm the task retains source provenance and disappears with source Forget or student reset. | Pending live-source qualification; reviewed import and cleanup are covered by synthetic tests. |
| Review and approval | Drafting/shortening saves or suggests text without sending. A changed recipient/source or expired review requires another review. The final request binds the displayed revision and payload hash. | Pending live-provider qualification. |
| Send and recovery | A single explicitly approved reply reaches the controlled recipient. Exact provider receipt verification establishes NavoX's sent state. A lost response remains unconfirmed; Check outcome performs receipt lookup and never resends. | Pending successful live Gmail/Microsoft send and exact receipt checks. |
| Outlook Waiting | After exact sent verification, a controlled later reply from the reviewed recipient in the same conversation retires Waiting. Earlier messages, drafts, self/outbound messages, aliases, third parties and unreferenced forwards cannot establish this proof. Incomplete scans stay unknown. | Pending live Microsoft conversation/header qualification. |
| Account and privacy | Switching owner suppresses late private responses. Pause/disconnect stops new provider work. Disconnect clears saved reply content and reply tracking metadata while preserving content-free execution receipts; Forget/reset erase the corresponding derived task copies. | Pending live-provider revocation/cleanup qualification; synthetic boundaries are separate evidence. |

### Selected email to task

The Outlook Inbox offers a task review for the **one selected, owner-readable
message**. `POST /outlook/task-reviews` takes the native connection ID, opaque
message ID, request ID and explicit preparation consent. The server rereads that
message through the current Microsoft grant, then uses the existing
`StudentEmailReview` correction and acceptance contracts. A saved task requires
the displayed review revision and source hash plus explicit confirmation of the
title, course decision, deadline decision and estimated effort. Preparation and
correction do not create a task or send mail.

The registered Outlook resource retains a source digest and opaque provider
locator; its canonical record does not retain the sender, subject or body.
The short-lived encrypted review holds the extracted evidence and the owner's
corrections, with the shared 20-minute review expiry. Accepted tasks use
`EmailTaskSource` provenance. A detected source digest change or revoked access
requires review before that copied task can be planned again. Disconnect clears pending
reviews and retains accepted tasks under the existing Gmail policy; source
Forget, connection Forget and student reset remove the associated task/source
copies and study blocks. This flow performs no provider write.

See the [Outlook task bridge](../../services/api/navox/outlook/tasks.py),
[shared review service](../../services/api/navox/student_email/service.py) and
[Inbox task review](../../apps/web/src/components/outlook-task-capture.tsx).

### Conservative meeting interpretation and availability

Gmail and Outlook use the already-read selected source, including its sent time,
for a bounded English-language meeting interpretation. The UI shows the exact
supporting subject/body passage and its assumptions. Relative dates are anchored
to the email's sent date in the saved time zone, rather than shifted forward
from the current day. Missing years, ambiguous numeric dates or clock changes,
multiple proposals, explicit foreign time zones, quoted changes and truncated
content require the owner to resolve the intended date and time. Task deadline
extraction does not supply meeting times.

The owner reviews or corrects the date, time and duration before checking
availability. A missing duration starts at a disclosed 30-minute value for
review. The subsequent check reads fresh calendar permissions, saved classes,
study blocks and routine; it preserves ten minutes on either side. Unknown
travel, unreviewed routine or incomplete calendar coverage stays unconfirmed.
At most two checked alternatives are offered. Results expire within 60 seconds
in the UI, and changing the source, owner, time or permissions retires the prior
check. Using a checked option only fills reply instructions; the final draft
still needs review and explicit Send. Reading an email or checking a time does
not create a calendar event.

See the [meeting interpreter](../../apps/web/src/lib/inbox-meeting-proposal.ts),
[meeting review](../../apps/web/src/components/inbox-meeting-check.tsx) and
[availability checks](../../apps/web/src/lib/inbox-availability.ts).

### Outlook coverage and outcome limits

Outlook checks **one page of at most 50 recent Inbox messages**, ordered by
received time. It surfaces possible requests and follow-up flags. Unsupported
or unreadable messages, including ambiguous multiple Reply-To addresses, stay
outside automatic single-recipient reply handling. Any skipped row or remaining
provider page marks coverage incomplete, including an otherwise empty candidate
result. The UI directs the owner to Outlook for the complete mailbox. It does not
follow the provider's next-page URL. Pending and completed source messages move
out of Needs reply. Unconfirmed sends have their own outcome-check section;
they are excluded from the Waiting count. This candidate scan refreshes while
the UI is active and does not claim to cover the entire mailbox.

Today and the navigation badge check at most three currently readable Outlook
mailboxes per refresh. Their counts describe returned reply candidates in the
checked mail. The UI discloses limited or failed coverage and links to Inbox,
where the student can choose another connected mailbox. Today passes only an
owner-bound mailbox/message navigation hint; opening it verifies permissions
again and reuses any existing reply review or send receipt.

Graph `202 Accepted` is not a verified sent result. Success requires the exact
message identifier and matching content fingerprint in Outlook Sent, followed
by `status:"completed"` and a non-null `verified_at`. This proves the matching
Sent record, not recipient delivery. Missing or ambiguous receipts remain
`executing`/`uncertain`; there is no automatic send retry. The saved review's
approval window is 15 minutes; private reply content becomes unavailable after
7 days. Draft/list access clears expired stored payloads, and disconnect clears
them immediately. Read-only receipt reconciliation can continue after reconnect
using the retained digest. This is not a promise that an unattended background
job physically deletes every expired draft at exactly seven days. See
[Outlook service](../../services/api/navox/outlook/service.py) and
[Microsoft adapter](../../services/api/navox/providers/microsoft_mail.py).

Reply tracking starts only after that exact send proof. It uses the provider's
actual sent time and conversation ID, plus a digest of the exact reviewed
recipient from the verified MIME. Each check reads one page of **at most 50
later message metadata records in that same mailbox/conversation**. It checks
the sender and From address, owner recipient, a strictly later received time,
and a References/In-Reply-To link to the verified outgoing Message-ID. Drafts,
self/outbound messages and automated reports do not retire Waiting. Alias,
delegate, forwarding or third-party ambiguity requires review in Outlook.
Truncation, unreadable records and inaccessible evidence remain unknown.

A complete bounded check with no matching later reply produces Waiting as of
its displayed check time. A matching later reply moves the local record into
**Sent replies**. That section represents replies sent through NavoX which have
a matched response; it does not represent the whole Outlook Sent folder.
Checks run after exact send verification and on **Check for replies**. While
Inbox is visible and no email/reply editor is open, the UI also checks at most
two current verified receipts in any minute, sequentially, rotating toward
older checks. Hiding Inbox, opening an editor or changing its owner/mailbox
aborts the current quiet check. Quiet results do not change the selected email,
active editor or tab. There is no monitoring while NavoX is closed.

Automatic checks require the same current native connection, unexpired private
content and existing sent/conversation/recipient evidence. The server checks
these conditions before and after provider reads; it never automatically
recovers metadata for old, disconnected or privacy-cleared records. The helper
which clears expired receipt content keeps the caller's transaction locks, so
manual checks also cannot restore reply metadata after a concurrent erasure.
Older or privacy-cleared
receipts can be checked explicitly after reconnecting the same protected mailbox;
the server first recovers the necessary metadata from the exact sent MIME.
Disconnect, connection Forget and student reset scrub the added reply metadata,
including records associated with an earlier native connection UUID for that
same mailbox. The durable content-free send fence remains.

See [reply reconciliation](../../services/api/navox/outlook/replies.py),
[privacy cleanup](../../services/api/navox/outlook/cleanup.py), and Microsoft's
[message list contract](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0)
and [message properties](https://learn.microsoft.com/en-us/graph/api/resources/message?view=graph-rest-1.0).

Mark these gates complete only after recording the live evidence. Credentials
being configured, a successful build, or a mocked send test does not close them.
