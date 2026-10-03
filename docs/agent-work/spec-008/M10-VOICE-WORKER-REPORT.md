# M10 voice session worker report

STATUS: ready_for_review
Task: `/root/spec008_voice_session`
Workspace: `/Users/mba_steins/NavoX` (branch `spec-008-navoxbot`, uncommitted, dirty with
accepted earlier SPEC-008 work preserved)
Brief: `docs/agent-work/spec-008/M10-VOICE-SESSION-BRIEF.md`

## Behaviour delivered

The click-to-talk session is now a real lifecycle with visible controls, and no late
browser callback can revive an ended session or speak a stale answer.

- `packages/contracts/src/assistant.ts` — `AssistantVoiceState` gains `MUTED` and
  documents that a voice state never carries approval, action or capability authority.
  `AssistantModality` stays input provenance.
- `packages/assistant-runtime/src/validate.ts` — `VOICE_STATES` includes `MUTED`.
- `packages/assistant-runtime/src/voice.ts` — `VoiceSessionState` gains the orthogonal
  `muted` flag plus `MUTE`/`UNMUTE` events and `automaticSpeechAllowed()`. Mute aborts
  only playback: an open microphone, a transcription and a pending answer keep running
  and the session rests as `MUTED`. `TRANSCRIPT`, `TRANSCRIPT_FAILED` and `SUBMITTED`
  are refused once the session is `STOPPED` or `UNSUPPORTED`; `SPEAKING_STARTED` is
  refused while muted/stopped/unsupported; `LISTENING_STARTED` is now a confirmed no-op
  so a stale `onstart` cannot reopen a session.
- `packages/assistant-runtime/src/presentation.ts` — speech is never offered for a
  decision that would need approval (`requires_approval` answers render but stay silent).
- `apps/web/src/lib/assistant-speech.ts` — adds the typed `synthesisReason` fallback and
  detaches a cancelled utterance's handlers before `speechSynthesis.cancel()`, so a
  cancelled utterance cannot report a late end or error.
- `apps/web/src/lib/assistant-controller.ts` — new `createVoiceSession()` owns the
  page-independent lifecycle: microphone, manual interruption, mute, stop, disposal,
  and a per-turn token that lets only a live answer speak. `voiceTurnRequest()` maps a
  transcript to `{ modality: "VOICE", text, referents: [] }` with no authority fields.
  `voiceControls()` is the single source for microphone/mute/stop/read-aloud state.
- `apps/web/src/components/navox-assistant.tsx` — the page now uses that session; Mute and
  Stop are always rendered controls, Mute cancels active TTS and suppresses later
  automatic speech, Stop cancels microphone and speech and holds the in-flight answer
  silent, a microphone click during playback cancels speech before listening (manual
  interruption, not acoustic barge-in), and the explicit Read aloud control is offered
  only while unmuted.
- `apps/web/src/components/navox-assistant.module.css` — status colour for `MUTED`/`STOPPED`.

Tests: `packages/assistant-runtime/src/voice.test.ts`, `presentation.test.ts`,
`apps/web/src/lib/assistant-speech.test.ts`, `assistant-controller.test.ts`,
`apps/web/src/components/navox-assistant.test.tsx` cover typed-turn silence, voice-turn
speech, mute during TTS, mute before the answer, unmute, Stop while listening and while
speaking, manual interruption ordering, late recognition/synthesis callbacks after clear
and disposal, unsupported microphone fallback, unavailable synthesis, and a spoken `yes`
remaining an ordinary read-only turn. The component tests are server-rendered markup
checks because the web workspace has no DOM test environment (no `jsdom`/Testing Library
dependency is installed and none was added).

## Verification (all exit status 0 unless stated)

- `npm run lint` (root, all workspaces) — passed; 9 pre-existing
  `noDescendingSpecificity` warnings in `subscriptions-dashboard.module.css`.
  One pre-existing `lint/complexity/useOptionalChain` error in
  `assistant-controller.ts` (`speech && speech.trim()`) blocked the gate and was fixed to
  `speech?.trim()`; that line predates this bundle.
- `npm run typecheck` (root, all workspaces) — passed.
- `npm run test` (root) — extension 8/8 passed; web 303/303 passed (37 files);
  assistant-runtime 220 passed, 5 skipped, 0 failed.
- `NAVOX_ISOLATED_BUILD=1 npm run build --workspace=@navox/web` — passed, all routes
  emitted. The isolated build rewrites `apps/web/next-env.d.ts`; it was restored to the
  committed `.next/types/...` references afterwards.
- Mutation checks (temporary local breakage, reverted) confirmed the new tests fail when
  the stop-token guard, the `automaticSpeechAllowed` guard, the stale-transcript guard, or
  the utterance-handler detach is removed.

The 5 skipped runtime tests are the `ASSISTANT_TEST_DATABASE_URL` PostgreSQL suites
(`store.integration.test.ts`, `migration.test.ts`). No PostgreSQL is reachable from this
sandbox (nothing on 127.0.0.1:5432; the Docker socket is denied), so those were not run.
No Python, migration, provider, lockfile, CI or SPEC-006/007 file was touched, and no
commit, push, deploy or provider call was made.

## Remaining gap (explicit)

Browser `SpeechRecognition`/`speechSynthesis` remains an unverified vendor prototype. It
does not satisfy the SPEC-005 provider boundary or SPEC-008's speech/streaming acceptance,
and this report does not claim SPEC-005 provider-backed speech, acoustic barge-in, wake
detection or a voice milestone. A microphone click during playback is a manual
interruption only. The next phase needs the authenticated SPEC-005 speech gateway before
the provider-backed voice path can be accepted; the currently registered SPEC-005 gateway
handles text, not audio.

## Decisions for Astra

- Whether the `MUTED` addition to `AssistantVoiceState`/`VOICE_STATES` should be reflected
  anywhere server-side (the runtime stores no voice state per turn today).
- Whether to authorise a disposable PostgreSQL stack so the 5 skipped runtime suites can
  run before the final gate.

## Next checkpoint

Unchanged from the brief: the provider-backed SPEC-005 speech gateway is the next phase;
no further work is pending inside this bundle.

## Correction pass (round 2)

Review findings addressed in the same TypeScript scope; no provider, backend, checkpoint,
lockfile or CI file was touched.

1. **Per-listen identity guard.** `createVoiceSession` now carries an `attempt` counter.
   `listen()` advances it and captures the new value; every `onTranscript`, `onError` and
   `onEnd` callback checks `!disposed && currentAttempt === attempt` before it may submit a
   transcript, show a notice or change state. `stop()`, the stop branch of
   `toggleListening()`, an explicit read aloud that takes over an open microphone, and
   `dispose()` all advance it. A callback from a stopped attempt can therefore neither
   submit nor stop the attempt that replaced it.
2. **Stop stays stopped through mute.** `mutedState()` now preserves `STOPPED` (and
   `UNSUPPORTED`, `LISTENING`, `TRANSCRIBING`, `THINKING`) and only sets the `muted` flag,
   so Stop then Mute then Unmute remains `STOPPED` and a stopped answer still cannot speak
   for itself.
3. **Read aloud control and lifecycle now agree.** Chosen behaviour: the explicit control
   stays available after Stop and when only the microphone is unsupported, including in
   the typed fallback. The reducer gained an explicit `READ_ALOUD_STARTED` event that is
   refused only while muted, while `SPEAKING_STARTED` keeps its strict guard, so automatic
   speech is still refused in `STOPPED`/`UNSUPPORTED`. `VoiceSessionState` gained
   `microphoneSupported`, which survives playback, so `voiceControls.microphoneDisabled`
   remains true while a read aloud plays in a browser that cannot capture audio; an
   unsupported session rests back in `UNSUPPORTED` when playback ends, and a read aloud
   after Stop rests at `IDLE` (the operator restarted audio deliberately). The controller
   now also only calls `adapter.speak` when the reducer accepts the transition into
   `SPEAKING`, so UI state and audio cannot disagree.

New tests: `voice.test.ts` ("keeps Stop terminal through mute and unmute", "plays an
explicit read aloud after Stop and without a microphone"), `assistant-controller.test.ts`
("ignores a stopped microphone attempt's transcript, error and end", "ignores the previous
attempt's callbacks after a new listen", "keeps a stopped session stopped through mute and
unmute", "plays an explicit read aloud after Stop and while the microphone is
unsupported", "releases the microphone when an explicit read aloud takes over").

Mutation checks (break, observe failure, revert) confirmed each new guarantee is covered:
removing the attempt comparison fails 3 controller tests; removing `STOPPED` from the
mute-preserving set fails 1 runtime and 1 controller test; refusing `READ_ALOUD_STARTED`
in settled states fails 1 runtime and 1 controller test.

Verification on the corrected tree (all exit status 0):

- targeted: `npm run test --workspace=@navox/assistant-runtime` — 222 passed, 5 skipped;
  `npm run test --workspace=@navox/web` — 308 passed (37 files).
- `npm run lint` (root) — passed, same 9 pre-existing `noDescendingSpecificity` warnings.
- `npm run typecheck` (root) — passed across all six workspaces.
- `npm run test` (root) — extension 8/8, web 308/308, assistant-runtime 222 passed / 5
  skipped / 0 failed.
- `NAVOX_ISOLATED_BUILD=1 npm run build --workspace=@navox/web` — passed; the generated
  `apps/web/next-env.d.ts` rewrite was restored to the committed `.next/types/...`
  references afterwards.

The provider-backed voice gap and the unreachable PostgreSQL suites are unchanged from the
section above.
