# M11 — SPEC-005 speech gateway design (next build)

## Result

Replace the browser-vendor speech prototype with a provider-backed path while
keeping the Assistant Runtime, voice-session orchestration, UI and transport
contracts in TypeScript. The existing Python SPEC-005 service is the unavoidable
provider dependency and remains the only code that holds provider credentials or
calls speech models. This document is a design checkpoint, not an implemented or
accepted speech path.

## Boundaries

- A TypeScript `SpeechGateway` has `transcribe(audio, signal)` and
  `synthesize(savedTurn, signal)` operations. `savedTurn` is a session-owned
  assistant turn selector, not arbitrary client-supplied text. The user may
  still type when the microphone, provider, or playback is unavailable.
- The browser captures only after an explicit mic action. Keep a bounded clip
  in memory, stop tracks on Stop, clear, unmount or timeout, and do not persist
  raw audio. A typed transcript is submitted to the existing assistant message
  endpoint as `VOICE`; it grants no action/approval authority.
- The Next API checks origin/authentication and exact session/turn ownership,
  then forwards bounded audio or saved spoken answer to authenticated SPEC-005
  speech routes. SPEC-005 applies the current operator/workspace/user provider
  grants, published model qualification, quota/cost bounds, fixed timeouts and
  fail-closed error mapping before making a provider call. An audio model is
  not eligible merely because a text model is registered or an API key exists.
- Transcript/speech metadata can be retained only under the assistant's
  existing retention rules. Raw audio and generated audio are transient.
  Responses use `no-store`; do not put text/audio in logs, URLs or exception
  messages. The spoken response must be derived from the exact saved answer,
  not a freeform client string.
- The first provider-backed path may use a bounded recorded clip. Streaming
  transcription, acoustic barge-in and app-open local wake detection are later
  acceptance phases; do not call a file-upload path streaming/realtime.

## SPEC-005 foundation required before integration

The current catalog has TEXT/EMBEDDINGS capabilities and text/embedding task
types, with token-based cost accounting. Audio needs explicit transcription and
synthesis capabilities/task types, qualified model registration, and usage
accounting appropriate to audio duration/characters or the provider's returned
usage. Design those within SPEC-005 rather than bypassing routing with a raw
OpenAI call. Deny by default until an operator publishes qualified audio model
assignments and personal-data grants. Preserve historical registry artifacts.

## Acceptance for the implementation phase

Exercise valid speech and synthesis with controlled provider fakes; deny
unqualified/ungranted models, unauthenticated and cross-user sessions/turns,
overlong or wrong-format clips, replayed/expired selectors, empty answers,
provider timeout/failure, and action-like transcripts. Verify that no audio
body reaches persistence/logging and Stop aborts client work. Only then run a
bounded live acceptance with an already approved provider inside the owner's
$10 total provider-call cap. No live call has been made for this design.

Official API reference consulted for a possible OpenAI adapter (provider
availability and account qualification remain unverified):
https://developers.openai.com/api/docs/guides/speech-to-text and
https://developers.openai.com/api/docs/guides/text-to-speech.
