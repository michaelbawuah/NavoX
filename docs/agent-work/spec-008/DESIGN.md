# SPEC-008 R3 — TypeScript runtime design

Source: `SPEC-008_R3_NavoXbot_Personal_Assistant_Runtime.pdf` (R2 behavior,
R3 implementation amendment). The owner's later removal of X from NavoX is
authoritative: X-specific examples in the PDF are excluded. This plan is an
implementation boundary, not a claim that all PDF acceptance scenarios pass.

## Objective and ownership

NavoXbot is a TypeScript orchestration layer over existing SPEC-001–007
capabilities. It owns one assistant session for typed and voice turns, bounded
intent/capability planning, presentation, action state, and voice state. Existing
services own their facts, permission checks, provider routing, approvals and
execution. NavoXbot never calls model vendors directly or treats a transcript,
retrieved document, or proposed plan as authority.

The existing Next.js Node runtime hosts `/api/v1/assistant/*` handlers; a new
TypeScript runtime package contains pure orchestration and adapter interfaces.
PostgreSQL retains assistant turn metadata and bounded content; the current
SPEC-005 `assistant_sessions` row is the session identity, created through its
existing authenticated `/ai/sessions` endpoint. A TypeScript SQL migration adds
only NavoXbot-owned tables with foreign keys to that row; it does not alter the
Python model or Alembic migration head. The TypeScript package owns an explicit
SQL migration command and a disposable-PostgreSQL schema check. This separate
schema must also be checked by its own CI path before deployment, since the
existing Alembic checker cannot assert tables outside its migration head.
Temporal workflows, when introduced, live in TypeScript using this same runtime
package and stable IDs.

## Trust and interface contract

- Client supplies only text, modality, a client request UUID and optional
  referents. It cannot supply workspace/user authority, a provider, an action
  grant, an executable tool name, or a trusted source assertion.
- Each API call resolves the current account by forwarding the HttpOnly session
  cookie to the configured same NavoX API `/auth/me`, and fences local rows by
  session, workspace and user. Writes require the configured same Origin and
  JSON content type. The upstream API URL is server configuration, never input.
- The first direct capability invokes `/today/query` with the original user
  question. SPEC-002 owns its intent classification and current projection.
  `unsupported` is a qualified response, not a reason to guess another route or
  silently buy a model call. Future general intent planning must consume a
  structured output from SPEC-005 and validate it in TypeScript. The current
  SPEC-005 API exposes saved-state `assistant` selection, not arbitrary intent
  planning; do not pretend it does either. No new Python code is needed for M1.
- The runtime validates structured plans against a server-owned capability
  registry. Supported actions must delegate to existing SPEC-001 approval and
  SPEC-003 execution APIs, with exact target/payload/version checks and an
  independently verified outcome. M1 exposes no consequential action endpoint.
- Persist at most 30 days of M1 question/Today response text, no audio or
  third-party snippets, with explicit session deletion. Future connected-source
  responses use selectors/IDs and reauthorize on replay instead of saving raw
  evidence text. Request UUIDs are idempotent within a session. A source failure
  returns qualified failure and never a fabricated answer.
- Browser microphone input is explicit click-to-talk. It and TTS use isolated
  browser adapter interfaces with visible listening/speaking/stop state and a
  typed fallback when unsupported. No wake detector runs in M1, no background
  listening or ambient audio streaming. The later wake adapter accepts a wake
  event only while the app is open and Hands-Free is enabled; native Swift/Kotlin
  adapters can replace it without altering runtime contracts.

## Stable shared contracts

`packages/contracts/src/assistant.ts` defines session, turn, bounded
`IntentPlan`, `CapabilityDecision`, structured response blocks, citations,
voice-session state, presentation plan, goal/action IDs and API DTOs. Keep the
shared contracts package type-only, matching its current convention; place
runtime validators in the assistant runtime package and call them at API and
adapter boundaries. Response states distinguish `READY`, `CLARIFY`,
`UNAVAILABLE`, `WITHHELD`, and action states. `TEXT`, `VOICE`, `BOTH` is a
presentation choice, never an authority signal. Validators reject unknown
route/tool values.

## Phases and acceptance boundaries

1. **M1 vertical slice:** durable shared session and turns, same-origin TypeScript
   API, direct Today capability, typed `/navox` chat, clicked microphone input,
   spoken Today answer, interruption/stop, clear/delete, no provider call.
2. **Connected intent and actions:** structured SPEC-005 intent bridge, bounded
   multi-intent/reference resolver, SPEC-007 evidence selectors, email draft and
   exact SPEC-001/003 approval/execution/verification, then meeting prep.
3. **Other domains:** subscriptions, Calendar/class navigation, News, general
   reasoning, compound and cross-domain turns. X stays absent. Each route keeps
   source authority and currentness in its owning service.
4. **Voice and activity:** realtime speech transport where supported, adaptive
   modality/barge-in, isolated local wake adapter, bounded activity/memory and
   TypeScript Temporal workflows. No always-on closed-app listening.
5. **Hardening:** adversarial auth/workspace/replay/approval and voice corpus,
   accessibility/privacy/latency measurements, full repository gates, hosted CI,
   live-source/provider and human acceptance evidence.

M1 completion does not imply full SPEC-008 completion. The PDF's later milestones
and targets remain open until their own behavior and measurements exist.
