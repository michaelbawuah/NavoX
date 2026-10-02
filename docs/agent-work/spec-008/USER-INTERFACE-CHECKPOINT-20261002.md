# SPEC-008 user interface checkpoint — 2026-10-02

The owner's website testing found that the app exposed implementation details
and made everyday controls difficult to understand. This checkpoint simplifies
the existing personal app; it adds no Team functionality or provider route.

## Product changes

- One Ask NavoX navigation destination replaces the duplicate NavoX/Assistant
  destinations. Today has a prominent entry to the saved conversation.
- The assistant leads with a short invitation and four optional question starters.
  Talk, Send, Stop and Read aloud have visible labels. The question box stays
  within the phone viewport during a long answer.
- Hey NavoX shows its current on/off state. Spoken-answer and mute preferences
  sit in a closed Voice options disclosure. A short microphone permission note
  is visible; chat retention and local wake/speech handling remain in Privacy.
- Connected apps and refresh controls sit under Settings. The normal day view
  omits confidence percentages, scoring factors, plan versions, replan counts,
  action types and risk codes. Source access and useful dates remain.
- Citations show readable source labels and guarded source links instead of
  record identifiers. Technical score lines are omitted from displayed details.
- Email review keeps the exact sender, recipient, subject, body, approval and
  rejection controls. Fingerprints, draft versions and provider message IDs
  are retained in the existing records but omitted from user-facing copy.
- News wording stays clear about the limits of trending and confirmation.

## Validation and integration

Runtime lint/typecheck and all 391 tests passed with disposable PostgreSQL and
Temporal, zero skips. Web lint/typecheck, all 416 tests and production build
passed. The existing Docker web image was rebuilt and updated normally.

An isolated, signed-in headless browser checked desktop and 390px phone layouts:
no page overflow, closed secondary options, question starter fills the input,
Send returns a READY headline answer, zero email action controls, and the phone
question box remains in view. The home Settings disclosure starts closed.
Private evidence includes screenshots and the bounded browser result; the test
conversation was deleted through the authorized session route (HTTP 200),
and the isolated browser/profile were removed. This does not prove microphone acoustics,
actual speaker playback, wake recognition or interruption on the owner's device.

Web, API live/ready and the normal owner session returned HTTP 200. This changes
no migration, credential, provider policy or execution/approval authority.

The full locked API preflight passed: 2,772 tests, zero skips, five retained
warnings; lint, formatting, strict mypy, schema/migrations, measured safety
metrics, deterministic release and patch whitespace all passed.
Hosted CI must be checked on the exact resulting commit.

## Release status

This is the Mac local integration app at http://localhost:3000/navox.
PR #22 is still draft. Current-revision drafting review, the controlled Gmail
acceptance record, Canvas credentials and a public NavoX hosting/domain deployment
remain separate mandatory release gates; this UI checkpoint does not declare
SPEC-008 complete or claim a deployment to navox.net.
