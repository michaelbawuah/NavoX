# SPEC-008 deployment checkpoint — 2026-10-02

Status: integration is blocked by credentials and current task qualification.
This is not SPEC-008 acceptance, a merge, or a production deployment.

## Completed during this continuation

- Confirmed branch `spec-008-navoxbot`, draft PR #22, head
  `98c5c6c77de64d2327ebcffb423f42100863827c`, and successful exact-head
  hosted CI run 36970693795 across all six required jobs.
- Added optional `PolicyRules.task_scopes`. Each entry binds workspace,
  user, task type, profile, prompt, schema, and sensitivity. A configured
  nonmatching or empty allowlist denies provider grants; omitted scopes
  retain existing behavior. This field only narrows the existing intersect
  of operator, workspace, user, and task grants.
- Activated a temporary planner-only API configuration for the synthetic
  canary principal. It grants OpenAI PERSONAL only, exact
  `PLANNING_HIGH / plan / assistant_intent_plan@v10 / schema@v9`,
  with a USD 0.01 request ceiling. No SENSITIVE grant, fallback, shadow,
  other principal, extraction, drafting, News, or TTS route was enabled.
- Saved-session compound request returned HTTP 200 / READY and the four
  expected weather, next-class, Today, and News intents. Unavailable
  domains remained qualified notices; this is not live-domain acceptance.
- The same session's ambiguous "Send it" returned HTTP 200 / CLARIFY,
  with zero action references. No controlled Gmail email was sent.
- Routing-focused tests: 68 passed; Ruff and strict mypy passed.
- The full locked API preflight completed successfully with disposable
  PostgreSQL/Temporal: all lint/type/schema/metric/release/whitespace gates
  passed. The exact test-count summary is retained in the local gate log.

## Recorded canary traces

| Task | Trace | Result |
| --- | --- | --- |
| `674ccd1f-1146-4ad7-962d-5378ea65c884` | `571c79be-b33e-448f-b3c0-1a095dccd4fe` | COMPLETED, four-intent request |
| `336ce9de-68d3-485d-8999-692d389dd7fc` | `5a56fa2a-8d14-4cef-83a7-2161dbdf3973` | COMPLETED, ambiguous send clarification |

Both attempts used OpenAI GPT-5.6 Luna, registry revision 9, planner prompt
v10 and schema v9. Combined recorded estimated cost: USD 0.0013676.

## Remaining blockers and limits

- The configured OpenAI credential has a recorded speech-generation
  permission failure. TTS is at 0% and has no qualifying revision-9 record;
  it must not be activated from transcription or text evidence.
- Only planner and STT have qualifying revision-9 evidence. Existing
  drafting/News/domain evidence from earlier revisions is retained, but
  is not current serving eligibility. No evidence was silently carried
  forward and no qualification thresholds were weakened.
- The owner completed normal login through the local helper. Auth/me
  verified the approved Gmail-connected account and exact workspace.
  The password was not persisted; the separate helper session is stored
  privately on the Mac and is not committed. The controlled self-send
  remains outstanding; no Gmail message was sent in this continuation.
- Existing application: local Docker on the owner's Mac, web at
  `http://localhost:3000/navox`. A separate public deployment target
  has not been identified. The visual motion preview is not this app.
- API and TypeScript migrations previously exited 0. Live Alembic head
  is `0034_knowledge_email_drafts`; assistant session/turn/goal tables
  exist. This policy change introduces no migration.
- API, web, PostgreSQL, and Temporal are running. The action worker is
  stopped, and the assistant-goals worker is not running. No stopped
  execution service was restarted merely to obtain a passing canary.
- Real microphone, audible TTS, wake recognition, and acoustic barge-in
  remain user/device checks. Hands-Free requires supported local speech
  recognition, an installed local language pack, and microphone permission;
  it does not fall back to cloud ambient wake recognition.
- The earlier owner decision deferred Canvas onboarding. The latest
  deployment request includes Canvas reconciliation again, so Canvas
  cannot be claimed complete from the deferred state; a reviewed
  institution OAuth deployment and connected course source are required.
- Team NavoX remains deferred. Broad R3 quality targets and the planner's
  retained one-case quality failure remain backlog; no new evaluation
  corpus, provider, optional refactor, or team feature was added.

The full locked API preflight passed. Hosted CI on the new commit must
still be checked before declaring this checkpoint ready. PR #22 remains
an integration draft until the credential and deployment blockers close.

## Owner-scoped integration after sign-in

The operator ceiling now includes PERSONAL and SENSITIVE solely for four
exact task scopes: the synthetic canary and the verified owner, each with
planner v10/schema v9 or transcription prompt v1/schema v1. The task/model
sensitivity gates remain intact; SENSITIVE does not permit text planning,
drafting, News, or synthesis. Fallback and shadow remain false; the ceiling
is USD 0.01 per request. Only the two currently qualified profiles are live.
Connected knowledge read access is enabled through the existing source
ownership/capability checks. Gemini and xAI remain excluded.

An authenticated owner compound turn returned HTTP 200 / READY with weather,
next-class, and Today intents, visual details and citations, zero action
references, and no unexpected speech. Notices remain where live sources are
unavailable. This is one bounded observation, not the unmeasured R3 targets.
A fresh existing synthetic TTS credential probe returned HTTP 401 /
missing_scope; no TTS qualification or audio activation was claimed.

Final local gate: 2,750 API tests passed, zero skips, five retained warnings;
all required lint, type, schema, metric, release, and whitespace checks passed.
The owner-authenticated synthetic WAV canary also returned HTTP 200 from
transcription, matched the fixture, produced a READY saved VOICE turn with
the time intent and visual answer, and created zero actions. This proves
provider STT/session integration, not a live microphone or audible TTS check.
