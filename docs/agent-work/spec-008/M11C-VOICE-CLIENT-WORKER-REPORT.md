# M11C worker report — TypeScript recorded-clip speech client

## Status

`ready_for_review`. Branch `spec-008-navoxbot`, baseline `afb0fe1d87a5cd680a64fc32bd844b6e4f84a2ad`.
No commit, push, deploy, provider call, Python change or migration was made.
Root owns the CI gates and final acceptance.

## What changed

The `/navox` microphone is now a bounded click-to-talk recorder that produces a
SPEC-005 WAV container and uploads it through a session-scoped Next route. The
prior browser vendor `SpeechRecognition` path is gone; `speechSynthesis` playback
is unchanged and remains the explicitly provisional browser TTS for the next
phase, not SPEC-005 TTS acceptance.

- `apps/web/src/lib/assistant-wav.ts` (new) — one dependency-free module for the
  SPEC-005 container contract: 16 kHz mono 16-bit uncompressed PCM WAV,
  200 ms–30 s, ≤ 1,000,000 bytes. It owns `resampleTo16k`, `encodeWavPcm16`
  and `parseAssistantWav` (chunk walk, frame-complete check, duration from
  frames, typed `SpeechClipError` copy). Bare PCM and WebM bytes are refused,
  never relabelled.
- `apps/web/src/lib/assistant-speech.ts` — recording adapter. `getUserMedia` +
  `AudioContext`/`ScriptProcessorNode` buffer float frames in memory; the
  operator's second click encodes one WAV clip and calls the injected
  `TranscribeSpeech(wav, signal)`. `getUserMedia` is invoked on its
  `MediaDevices` receiver, so a browser brand check cannot reject the bare
  function reference. `finishListening()` reports whether this click really
  started an upload, and the page only claims transcription when it did.
  Capture stops at the contract's 30 s ceiling on both buffered frames and the
  wall clock: the graph closes, every track stops, and the operator gets bounded
  typed copy rather than a clip SPEC-005 would refuse. Stop, clear and unmount
  abort the `AbortController` (killing an in-flight fetch), disconnect the
  graph, close the context and stop every track; a stream that resolves after
  the operator already stopped is stopped immediately. Every failure path is a
  typed, copy-safe fallback that leaves typed input usable. Capabilities now
  read `captureSupported`/`captureReason`.
- `apps/web/src/lib/assistant-client.ts` — `transcribeAssistantSpeech` posts the
  raw `audio/wav` body with `credentials: "include"` and `cache: "no-store"` to
  `/api/v1/assistant/sessions/<id>/speech/transcribe`, carries the abort signal,
  and accepts only a non-blank transcript of at most 500 characters. No
  provider, model, credential, URL or scope field can travel from the browser.
- `apps/web/src/lib/assistant-controller.ts` — `VoiceSessionDeps` gains the
  session-scoped `transcribe` uploader, which is passed to the capture attempt.
  The microphone control is now a start/finish toggle: the first click opens the
  microphone, the second finishes and transcribes, and the separate Stop control
  cancels capture or the in-flight upload. A successful finish dispatches the
  new `TRANSCRIPTION_STARTED` event, so the session shows `TRANSCRIBING` while
  the clip uploads, the finish affordance is disabled, and Stop stays available.
  The existing attempt/generation fencing is preserved, so a late transcript
  after Stop, restart, clear or unmount still cannot append a turn.
- `apps/web/src/components/navox-assistant.tsx` — binds the uploader to the live
  session through a ref, and updates the recording copy, the start/finish
  microphone label, the disabled-while-transcribing microphone control and the
  Stop semantics. Typed input is untouched.
- `packages/assistant-runtime/src/voice.ts` — one new minimal state event,
  `TRANSCRIPTION_STARTED`. It moves only an open `LISTENING` session to
  `TRANSCRIBING` (transcript cleared) and is a no-op everywhere else, so a
  duplicated click, a late callback or a click after Stop cannot move a settled
  session.
- `apps/web/src/app/api/v1/assistant/sessions/[sessionId]/speech/transcribe/route.ts`
  (new) — same-origin `audio/wav` mutation guard, session cookie, streamed body
  bounded at 1,000,000 bytes before buffering, local WAV contract validation,
  `getAssistantRuntime().readSession` ownership proof *before* any audio leaves
  the process, then one fixed 30 s POST to the server-configured
  `NAVOX_API_BASE_URL` `/ai/assistant/speech/transcribe` with `redirect: "error"`
  and a bounded response read. It returns only `{text}` with `no-store` and maps
  every failure to a bounded assistant error; upstream bodies and audio are
  never echoed.
- `packages/assistant-runtime/src/http.ts` — `assertSameOriginMutation` accepts
  an optional `expectedContentType`/`contentTypeMessage`. JSON stays the
  default, so no existing route changes behavior; the audio route opts into
  `audio/wav`.

## Tests

New/updated coverage: `assistant-wav.test.ts`, `assistant-speech.test.ts`,
`assistant-speech-route.test.ts`, `assistant-controller.test.ts`,
`assistant-client.test.ts`, `navox-assistant.test.tsx`,
`packages/assistant-runtime/src/http.test.ts`,
`packages/assistant-runtime/src/voice.test.ts`.

Mapped to the brief's evidence list:

- 16 kHz WAV wire bytes and duration: `assistant-wav.test.ts` asserts RIFF/WAVE
  fields, PCM format, byte rate, clamp behaviour, resampling from 48 kHz, and
  the 200 ms/30 s bounds; `assistant-speech.test.ts` proves the exact uploaded
  bytes parse as 16 kHz mono 16-bit 1,000 ms.
- microphone → WAV → route → transcript → one VOICE turn: the
  `recorded-clip voice flow` test wires the real adapter, real client (stubbed
  fetch), real controller and a stub `submit`, and asserts one transcript, one
  `VOICE` turn, an empty referent list, and a turn payload with no approval,
  action, provider or model authority.
- session ownership and cross-origin denial: the route test proves 403/401
  before the runtime or upstream is touched, and that a session-scoped
  `readSession` failure returns 404 with no upstream call.
- wrong content type / oversize / short clip: route test (JSON body, 120 ms
  clip, 1,000,001-byte body) and the WAV test's container checks.
- Stop/clear/unmount abort with zero late turn: adapter tests assert the abort
  signal and track release; controller tests assert no late transcript becomes
  a turn after Stop or dispose, and that a transcript the page refuses (for
  example while another turn is busy) releases the session instead of leaving
  it resting mid-transcription.
- upload state and ceiling: `voice.test.ts` proves `TRANSCRIPTION_STARTED` moves
  only an open listening session and is a no-op for idle, muted, speaking,
  stopped, unsupported, already-submitted and already-transcribing states; the
  controller tests prove the second click shows `TRANSCRIBING` with Stop live,
  a recorder that never started an upload keeps `LISTENING`, a failed upload
  releases the session, and a second finish click cannot run twice; the adapter
  tests prove the microphone closes at the 30 s frame ceiling (exact frame
  input) and at the wall-clock backstop (fake timers, no frames) with bounded
  typed copy, stopped tracks and no upload.
- receiver brand check: the brand-check test defines `getUserMedia` on the
  `MediaDevices` object and throws unless `this` is that object, so a bare
  function reference would fail the test.
- provider timeout, malformed response and no provider capability: route tests
  cover the 30 s abort, unparsable/overlong/blank envelopes, and a 503 mapped to
  a typed `unsupported` fallback; the client test proves the typed refusal text
  reaches the UI.
- no audio retention or logging: adapter and route tests assert no console or
  storage writes and that only `{text}` leaves the route.

## Verification

All commands ran on the complete working tree at the paths below; full logs are
kept in the workspace.

| Command | Exit | Result |
| --- | --- | --- |
| `npm run lint` (repo root, all workspaces) | 0 | Web 147 files clean apart from 9 pre-existing `subscriptions-dashboard.module.css` `noDescendingSpecificity` warnings; assistant-runtime 52 files clean |
| `npm run typecheck` (repo root) | 0 | web, assistant-runtime, connector-sdk, contracts, ui |
| `npm test` (repo root) | 0 | extension 8/8; web 345/345 in 39 files; assistant-runtime 228 passed, 5 skipped, 1 skipped file |
| `npm run build --workspace=@navox/web` | 0 | Next compiled; `/api/v1/assistant/sessions/[sessionId]/speech/transcribe` emitted as a dynamic route |
| `npx vitest run` on the six M11C files (web) | 0 | 102/102 |
| `npx vitest run src/voice.test.ts src/http.test.ts` | 0 | 32/32 |

Logs: `/tmp/m11c-final-lint.log`, `/tmp/m11c-final-typecheck.log`,
`/tmp/m11c-final-test.log`, `/tmp/m11c-web-build.log`,
`/tmp/m11c-focused-web-tests.log`, `/tmp/m11c-http-test.log`.

The 5 skipped assistant-runtime tests are the disposable-PostgreSQL
`store.integration.test.ts` cases; they need a live database stack that this
slice did not start. No browser device run was performed: the audio path is
verified against controlled media fakes, not a real microphone, and
`SpeechRecognition` behaviour that the previous M10 slice exercised is no longer
part of the client. `bash scripts/check-api.sh` was not run: this bundle is
TypeScript-only, the brief scopes verification to the root JS gates plus the
Web build and route/security tests, and Python is out of scope here.

## Risks and limitations

- The audio path is fake-tested, not device-tested. `getUserMedia`, a real
  `AudioContext` sample rate and browser permission UX still need a manual or
  hosted check before this can be called live-accepted.
- `ScriptProcessorNode` is deprecated and runs on the main thread. It is chosen
  for a bounded 30 s clip because `MediaRecorder` cannot produce PCM/WAV and
  `MediaStreamTrackProcessor` is not broadly supported. An `AudioWorklet`
  replacement is a follow-up if main-thread cost becomes a problem.
- At the ceiling the recording is discarded with typed copy instead of being
  auto-uploaded. The frame cap is the exact 30 s contract bound; the wall-clock
  timer is a 250 ms-later backstop for a stalled audio graph. A clip recorded to
  the limit therefore asks the operator to record a shorter question rather
  than silently truncating it.
- During upload the session shows `TRANSCRIBING` with the microphone control
  disabled and Stop available; the shared `voice.ts` addition is one event, and
  the existing `TRANSCRIPT`/`SUBMITTED` path is unchanged.
- The Next proxy timeout is a fixed 30 s while the SPEC-005 provider read
  timeout defaults to 120 s. A slow-but-legitimate provider answer is therefore
  cut off here as a retryable typed fallback rather than waiting. That and the
  ceiling discard above are the two deliberate bounded-UX choices worth an
  explicit keep-or-change from root.
- SPEC-005 remains fail-closed without a published, granted audio model, so a
  live end-to-end transcription still requires the operator's provider grants.
  No paid or live provider call was made.

## Next checkpoint

Root review of this bundle, then one browser/device pass on `/navox` once
SPEC-005 has an eligible audio model, together with the CI gate on the final
head.

## Root acceptance review

Root review added propagation of the incoming request's abort signal to the
fixed SPEC-005 upstream request. The route test proves a cancelled upload
aborts that request and does not echo the transport failure. On the complete
M11C tree, root lint/typecheck, 346 Web tests, 233 PostgreSQL-backed runtime
tests, both builds and the 29-assertion TypeScript migration check passed.
`bash scripts/check-api.sh` passed with 2,687 API tests and zero skips against
the disposable PostgreSQL/Temporal stack; see
`/tmp/navox-spec008-m11c-api-gate.log`. This accepts the implementation slice
for branch publication, not a real-device or provider-backed voice session.
