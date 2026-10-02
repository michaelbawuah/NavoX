# SPEC-008 deployment checkpoint — 2026-10-02

Status: local voice integration works; mandatory acceptance and public deployment remain pending.
This is not SPEC-008 acceptance, a merge, or a production deployment.

## Latest integration state

This section supersedes the earlier credential and worker status below.
The earlier observations remain historical evidence, not current limitations.

- Source parent 3b45963776e4ac73595156ea3bd9a0821ab722e0 is pushed.
  All six hosted CI jobs passed in run 37010958637. PR #22 remains draft;
  SPEC-008 has not merged and no public deployment is claimed.
- The owner's speech-generation permission now works. Revision-9 TTS
  qualification reused the existing 12 synthetic speech fixtures: 12/12
  decoded and passed bounded STT-roundtrip checks, p95 2,150 ms, reserved
  cost USD 0.0068694. This is not a real-device acoustic/accessibility score.
  Report SHA-256: cf263a7af0ec9cb1ab799d43a495502de37f7ff45f4861f01a444c015a6f8420.
- TTS canary observed successes at 5/25/50/100%, rollback to 0% denied
  synthesis, and the observed stages were restored within the allowed scope.
- Only qualified OpenAI planner, STT and TTS bindings are enabled for the
  verified owner and synthetic canary: six exact principal/task scopes.
  Planning binds v10/schema v9; speech binds v1/schema v1. The USD 0.01
  request ceiling, sensitivity enforcement, no fallback and no shadow remain.
  Drafting, News, Gemini, xAI and RESTRICTED routing are not enabled.
- The same scoped policy is persisted in the private untracked runtime
  environment. Only AI_PROVIDER, AI_PROVIDER_POLICY and KNOWLEDGE_ENABLED
  changed. The effective API environment matched the tested override exactly.
  Policy SHA-256: 0f45cb583ba4c08d563089e5184edaf3d052bff899676014a3a6277be0210103.
  A private backup/hash audit was preserved; no credential was committed.
- Owner saved-session synthetic voice flow: transcription 200 and fixture
  match; time answer READY with zero actions; saved-answer synthesis 200,
  audio/mpeg, 90,240 bytes; typed follow-up READY, sequence 2, silent.
  Session e0b7ce24-45d5-4d79-bbbe-0d2b3a04883d;
  voice turn f32cd5bf-10dc-49c6-a262-96945491ed27.
- Web /navox and API live/ready returned 200, with database and Temporal
  ready. The normal owner session returned 200. Both navox-foundation and
  navox-assistant-goals have active workflow/activity pollers.
  Existing migrations remain at 0034_knowledge_email_drafts; no new migration.
- The existing 16-case current-revision drafting run completed, with 16
  schema-valid successful provider results. It is not recorded or promoted:
  the validator requires human review of this exact report. The owner review
  sheet preserves every source, instruction, prior edit and exact candidate.
  Report digest: 9e3819e82d1f740c7f667059a2c018c907e67476a08f6a356cc445dc455d9558.
- No controlled Gmail send occurred in this continuation. Historical uncertain
  sends must not be retried. The exact approval/send/provider-verification
  acceptance remains pending after drafting eligibility.
- Public NavoX hosting/domain routing and Canvas source credentials remain
  unresolved; News is disabled. navox.net registration does not establish an
  application deployment. The unrelated MarketLab project was not modified.
- The owner's actual page screenshot exposed tiny voice-control icons.
  A minimal CSS specificity fix restores zero padding on compact buttons and
  prevents SVG shrinkage. Web lint/typecheck, 416 tests and production build
  passed; the local Docker web was rebuilt and serves the corrected CSS.
- Full locked API preflight for this CSS/documentation tree passed: 2,750
  tests, zero skips, five retained deprecation warnings; all lint/type/schema,
  metrics, deterministic release and whitespace gates passed.
  Real microphone/playback/wake/barge-in remain device checks for the owner.

## Earlier completed observations

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

## Earlier blockers and limits (historical)

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

## Earlier owner-scoped integration after sign-in

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
