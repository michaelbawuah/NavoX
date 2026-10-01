# SPEC-008 checkpoint — 1 October 2026

Branch `spec-008-navoxbot` began on the SPEC-006/007 PR #21 head
`bb0a7c96f4f9a7a6e9acb50fad431f8c546c6256`. The SPEC-008 foundation was
committed as `7c6f40dfaa274ce2cb27384e105a8ad0fae9618c` and pushed to
draft PR #22. Preserve unrelated untracked SPEC-006/007 validation material.
PR #21 was merged into `main` on 1 October 2026 as
`5513526ba3e7bba8543e121f006c1366dcb94d86` after all six PR CI jobs
passed on the exact PR head. Post-merge CI run 208 also completed successfully
on that exact merge SHA: API, Web, Chrome extension, dependency security,
evaluation/hardening and Compose integration all passed. This is an
implementation checkpoint, not full original-spec or
live-provider acceptance for SPEC-006/007.

## PDF milestone status (R3)

The implementation reports below use **local development slice** names M1–M10;
these are not the PDF's broader M1–M11 product milestones. No PDF milestone is
accepted end to end yet. The current mapping is:

| PDF milestone | Current status | Principal remaining work |
| --- | --- | --- |
| M1 Assistant Core | Partially built locally | Full assistant behavior and acceptance across text/voice/UI |
| M2 General Conversational Understanding | Partially built locally | General reasoning, richer temporal/reference handling, live cross-domain turns |
| M3 Connected Intelligence | Partially built locally | Live authorized sources, grounded retrieval evidence, safe navigation |
| M4 Goals & Planning | Open | Bounded goals, plans and TypeScript Temporal workflows |
| M5 Safe Actions | Partially built locally | General action orchestration, complete audit/verification and adversarial acceptance |
| M6 Communication Assistant | Partially built locally | Live Gmail thread/draft/send verification and cross-domain flow |
| M7 Voice Input & NavoX Voice | Partially built locally | Streaming speech, provider-routed voice, two-way session acceptance |
| M8 Adaptive Conversation | Partially built locally | Provider-backed modality switching and acoustic barge-in with retained context |
| M9 Hey NavoX Hands-Free | Open | In-app wake detection and visible state; only an isolated adapter contract exists |
| M10 Personal Intelligence & Briefings | Partially built locally | Live Today/meeting/News/Calendar evidence and coherent briefings |
| M11 Production Hardening | Open | Security/voice corpus, accessibility, latency/quality targets and hosted CI |

The owner removed X from NavoX scope, superseding the PDF's X-specific items.

## Accepted local slice: M1

The TypeScript runtime, shared contracts, authenticated Next API, PostgreSQL
session/turn/claim storage, read-only SPEC-002 Today adapter, click-to-talk UI,
and inert wake-word adapter are implemented. SPEC-005 owns the session identity;
NavoXbot never imports a provider SDK. Scope is derived from `/auth/me` on each
request. No consequential action route exists. Root review added a database turn
cap and replay behavior at the cap, generic error responses, Compose wiring and
CI schema checks. `M1-REPORT.md` records design and limits.

Validation on the complete M1 code tree: `bash scripts/check-api.sh` passed with
the current `uv.lock` and a disposable PostgreSQL/Temporal test stack: 2,532 API
tests passed, zero skipped, followed by metric thresholds, Alembic SQL/schema,
deterministic release evaluation and whitespace checks. Root npm lint,
typecheck and tests passed (275 Web, 112 runtime without DB, 8 extension);
isolated Web and extension builds passed. With disposable PostgreSQL, the
runtime suite passed 117 tests and the TypeScript migration check passed 29
assertions. An isolated Compose authentication/Today/session/history/origin/
clear smoke passed. See `/tmp/navox-spec008-api-gate-final-20260930.log` and
`/tmp/navox-spec008-js-gate.log` for local gate logs.

These are local results, not full SPEC-008 acceptance. Browser-vendor speech
recognition may use a remote service outside SPEC-005; no device/browser voice
observation, live wake detector, paid provider run or hosted CI exists for this
branch. At the M1 checkpoint, SPEC-006/007 parent PR #21 had a hosted
Evaluation & hardening job blocked before runner assignment by GitHub billing.
After the account limit reset, its rerun and all five other PR jobs passed on
the exact head, and the PR was merged as recorded above.

## Accepted local slice: M2

The existing Python SPEC-005 gateway now registers a bounded structured-intent
task and exposes an authenticated planning endpoint. The TypeScript runtime
validates the response envelope and plan, resolves only server-owned read-only
routes, and uses SPEC-007 search for email evidence. A single complete match
can be shown with exact selectors; ambiguity or incomplete coverage asks for
clarification. No email send, approval, scheduling or other consequential route
was added. `M2-REPORT.md` records the wire contract and limitations.

The M2 full API gate passed on its Python tree with disposable PostgreSQL and
Temporal: 2,546 tests, zero skipped, plus all schema, metric, evaluation and
whitespace checks. After the TypeScript envelope/coverage correction, root npm
lint/typecheck/tests and the Web build passed; the runtime database suite
passed 160 tests without skips. The corrected TypeScript tree has not yet had
a hosted CI run or live provider acceptance. The planner needs an operator
catalog publication and qualified provider assignment before live use; none
was activated or charged.

## Resume

M3A has locally established the existing-service email draft/approval bridge;
see `M3A-REPORT.md`. Its full API preflight passed 2,576 tests with no skips.
The Flash route ran out of balance mid-bundle, so Astra completed the partial
patch directly. No assistant action API/UI has been added yet.

M3B now adds local TypeScript exact email draft/approval/execution orchestration
on top of that service contract; see `M3B-REPORT.md`. Web and runtime checks are
green and the full PostgreSQL/Temporal API gate passed on the M3B tree. The action remains unverified
against a live authorized Gmail account and qualified SPEC-005 provider.

M3C adds explicit selection of one ambiguous `EMAIL` candidate, with a
session-owned source-turn check and a fresh complete SPEC-007 search before
the selected turn can expose the existing draft action; see `M3C-REPORT.md`.
Root JS lint, typecheck and tests plus Web production build passed locally.
No schema or Python service changed in M3C. This is still an uncommitted local
SPEC-008 branch, not a full SPEC-008 acceptance.

M4 adds read-only structured meeting preparation from the existing authenticated
`/proactive/meeting-prep` service; see `M4-REPORT.md`. The runtime binds its
meeting ID, title and start time to the SPEC-002 Today meeting result before
display. This remains local and unverified with live Calendar/connected sources.
On the combined M3C/M4 tree, root lint/typecheck/tests and Web production build
passed: 281 Web tests, 165 runtime tests with five DB-only skips, and eight
extension tests. No Python or schema change was made in these two slices.

M5 adds read-only SPEC-004 subscription answers; see `M5-REPORT.md`. The
runtime verifies a named merchant in the user's utterance, requires one
candidate, and separates recorded renewal, verified cancellation and unknown
access end. Its full API gate passed 2,577 tests without skips, and the runtime
PostgreSQL integration suite passed 179/179. No live subscription provider
observation was made.

M6 adds read-only SPEC-006 News trends and source-backed story summaries;
see `M6-REPORT.md`. M7 adds current workspace weather and exact
question-span, read-only compound routing; see `M7-REPORT.md`. The active
planner is `assistant_intent_plan@v7`, with v1-v6 historical artifacts intact.
Root JavaScript lint, typecheck, tests and Web build pass. The full API gate
with PostgreSQL and Temporal passed 2,586 tests without skips on the combined
tree at `/tmp/navox-spec008-class-time-combined-api-gate.log`. M8 adds
local Canvas/Google Calendar next-class normalization and a four-domain
scenario H synthetic test;
see `M8-REPORT.md`. Production connector paths retain locator-only records,
so M8 now includes a bounded permission-checked transient connector preview
that does not retain source bodies or advance sync cursors. A subsequent local
extension adds guarded Open Calendar/Open Class links with a fresh permission
check at click time, and M9-TIME adds an injected-clock `time.now` direct route
with explicit UTC fallback. The combined runtime PostgreSQL tests pass 217/217,
and the migration check passes 29 assertions. Live source/provider
acceptance, natural-language referent navigation, voice/wake, activity/Temporal,
and security/quality targets remain. Rerun gates on the final tree before any
push. Remove the disposable `navox-spec008-gate` stack when integration tests
no longer need it.

M10 adds local click-to-talk voice-session lifecycle controls after the M9 time
utility: visible Mute and Stop, manual microphone interruption of playback,
explicit Read aloud, and callback identity checks across Stop/restart/clear.
See `M10-VOICE-SESSION-BRIEF.md` and `M10-VOICE-WORKER-REPORT.md`. Root review
found and corrected stale listening callbacks, Stop-to-Mute state drift, and
Read aloud/state mismatch. Final root npm lint, typecheck, tests and isolated
Web build pass (308 Web tests, 222 runtime tests plus 5 PostgreSQL-only skips,
8 extension tests); `git diff --check` passes. This does **not** accept the PDF
voice milestones: browser speech remains a vendor-dependent prototype, with no
SPEC-005 audio route, acoustic barge-in, or active wake detector. No paid call,
commit, push or hosted CI run was made for this slice.
`M11-SPEECH-GATEWAY-DESIGN.md` records the provider-boundary contract and its
default-deny qualification requirements. M11A implemented audio capability,
profile, duration-based pricing and exact qualification behind SPEC-005;
see `M11A-AUDIO-WORKER-REPORT.md`. M11B now adds a bounded WAV transcription
route through the same gateway; see `M11B-TRANSCRIPTION-WORKER-REPORT.md`.
The correction review fixed the WAV container sent to the provider, upload
buffer bound, response model check and membership recheck at quota admission.
Focused M11B tests pass (63), as do Ruff lint/format and strict mypy. The worker's
full API gate had 2,679 passing tests and eight infrastructure-dependent skips;
its two metric checks therefore failed. Root reran `bash scripts/check-api.sh`
against disposable PostgreSQL/Temporal on the corrected tree: exit 0, 2,687
tests passed with zero skips, and every lint, type, metric, schema, release
evaluation and whitespace gate passed. The log is
`/tmp/navox-spec008-m11b-root-api-gate.log`. No speech profile, provider grant or paid call
was activated. The TypeScript voice client, synthesis, active wake detector,
acoustic barge-in, live source/provider acceptance and green hosted CI for SPEC-008
remain outstanding.

## Foundation publication and first hosted correction

Draft PR #22 opened on the foundation commit. Its first hosted CI run
`36821483043` passed Web, Chrome extension, API, dependency security and
evaluation/hardening, but Compose failed in the new assistant lifecycle smoke.
SPEC-002 returned eleven published `supported_queries`; the TypeScript Today
validator allowed only ten, so it rejected an otherwise valid grounded answer.
The regression test first reproduced that failure. The correction raises the
bounded suggestions contract to sixteen and preserves rejection beyond that
limit. Root npm lint/typecheck/tests and Web build passed; all 228 assistant
runtime tests passed against disposable PostgreSQL. The full API preflight
passed again with 2,687 tests and zero skips on the correction tree; see
`/tmp/navox-spec008-foundation-ci-fix-api-gate.log`. Correction commit
`afb0fe1d87a5cd680a64fc32bd844b6e4f84a2ad` was pushed to the draft PR.
Hosted CI run `36828936849` completed on that exact head with all six jobs
successful: Web quality, Chrome extension quality, API quality, dependency
security, evaluation/hardening and Compose integration. The foundation is now
a committed and CI-green branch checkpoint, while PR #22 remains draft because
the broader R3 voice, wake, workflow, live-source and action acceptance gates
are still open.

## M11C microphone client under acceptance

`M11C-VOICE-CLIENT-BRIEF.md` and `M11C-VOICE-CLIENT-WORKER-REPORT.md` describe
the TypeScript recorded-clip path: browser microphone to bounded 16 kHz WAV,
same-origin session route, SPEC-005 transcription and one VOICE AssistantTurn.
Root review corrected browser `MediaDevices` receiver binding, upload-state
display, the 30-second microphone ceiling and abort propagation from a
cancelled Next request to the upstream call. Root JavaScript lint/typecheck,
346 Web tests, 233 runtime tests with disposable PostgreSQL and Web/extension
builds pass on the current working tree. The TypeScript migration check passes
29 assertions. The full API pre-push gate passed with 2,687 tests and zero
skips, plus all lint, type, schema, metric, release and whitespace checks;
see `/tmp/navox-spec008-m11c-api-gate.log`. A real
microphone and a qualified live speech provider have not yet been exercised.
`M11D-TTS-BRIEF.md` defines the next provider-backed speech phase.
