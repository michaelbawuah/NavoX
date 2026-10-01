# M11C — TypeScript microphone to SPEC-005 transcription

## Objective

Wire one explicit, bounded click-to-talk recording through the existing
SPEC-005 `POST /api/v1/ai/assistant/speech/transcribe` route and submit its
returned text as a `VOICE` AssistantTurn in the current session. Preserve typed
input. This is the recorded-clip STT phase; it does not claim streaming,
provider-backed TTS, acoustic barge-in or wake detection.

## Ownership and constraints

One implementation worker owns `apps/web/src/lib/assistant-speech.ts` and its
tests, the assistant client/controller/page and their tests, a new Next assistant
speech route and route tests, and the small shared same-origin HTTP guard needed
for `audio/wav`. The worker may add a narrowly scoped TypeScript helper and
tests under `apps/web/src/lib/`. Do not edit Python SPEC-005 routing, catalog,
quota or provider code, other SPEC-001–007 services, CI, migrations, or unrelated
files. The worker is not alone in the repository: preserve every existing edit
and do not revert another contributor's work. No commit, push, deploy, real
provider call or worker delegation.

## Contract

- A microphone click starts capture only while the `/navox` page has an active
  assistant session. The second microphone click *finishes* and transcribes
  that clip; the separate Stop control aborts capture or an in-flight fetch
  without submitting a turn. Clear/unmount also aborts and releases tracks.
  Preserve `VoiceSession` callback fencing so a stale transcript cannot append
  a turn after Stop, restart, clear or unmount.
- Capture locally with browser media APIs, encode an uncompressed PCM WAV
  container, mono, 16 kHz, 16-bit, 200 ms–30 s and at most 1,000,000 bytes.
  Resample when the device sample rate differs; never label bare PCM or WebM
  as WAV. No raw audio in storage, logs, URL or persistent state. If capture,
  resampling or encoding is unavailable, give a typed-input fallback.
- The browser calls a same-origin Next route scoped by the current session ID,
  for example `/api/v1/assistant/sessions/[sessionId]/speech/transcribe`.
  The Next route requires the existing session cookie, exact same-origin
  mutation check for `audio/wav`, and `getAssistantRuntime().readSession` before
  forwarding. Bound the streamed body before buffering, use only the
  server-configured SPEC-005 base URL, forward the cookie and validated WAV
  under a fixed timeout, reject redirects and malformed/overlong responses,
  and return only `{text}` with `no-store`. Map failures to bounded assistant
  errors without exposing upstream body or audio.
- The returned transcript is plain question text, at most 500 characters.
  Submit through the existing assistant message route with `modality: VOICE`
  and no action, approval or referent authority. No caller-selected provider,
  model, credential or URL.
- Browser `speechSynthesis` remains the existing provisional playback for this
  phase; do not present it as SPEC-005 TTS acceptance. The next phase replaces
  it with provider-backed speech from an exact saved assistant answer.

## Acceptance evidence

Use controlled media and fetch fakes to prove: 16 kHz WAV wire bytes and
duration; microphone → WAV → Next route → transcript → one VOICE turn;
session ownership and cross-origin denial; wrong content type/oversize/short
clip denial; Stop/clear/unmount abort with zero late turn; provider timeout,
malformed response and no provider capability handled with typed fallback;
no audio retention or logging. Run root lint, typecheck, tests, Web build and
relevant route/security tests. Record exact results and any browser limitations
in a worker report. Do not run a paid live provider call.
