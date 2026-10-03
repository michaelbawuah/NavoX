# M10 — Voice session and adaptive presentation

## Decision and scope

SPEC-008 remains a personal assistant in the existing TypeScript runtime. This
phase makes the current optional click-to-talk session predictable and visible:
typed turns stay silent, voice turns show text and offer speech, mute suppresses
playback, Stop releases the microphone and speech, and a new operator turn can
interrupt playback without stale callbacks restoring an old state. A voice
transcript is only user input; it never approves an action. Keep the wake-word
adapter isolated and inactive in this phase.

The existing browser SpeechRecognition/speechSynthesis adapter is a prototype.
It may delegate speech to a browser vendor and does not satisfy the SPEC-005
provider boundary or SPEC-008's full speech/streaming acceptance. Do not expand
that dependency or claim the voice milestone complete. The next phase will add
an authenticated SPEC-005 speech gateway before the provider-backed path is
accepted; the current SPEC-005 registered gateway handles text, not audio.

## Contract

- Keep `AssistantModality` as input provenance, separate from presentation and
  separate from action authority. A voice response may be rendered visually
  when muted. A typed turn never starts speech automatically.
- `VoiceSessionState` must distinguish idle, listening, transcribing, thinking,
  speaking, muted, stopped and unsupported states in the runtime/contract as
  needed. Preserve legal transitions and ensure late recognition or synthesis
  callbacks cannot revive an ended/cleared session or speak stale answers.
- Mute and Stop are visible, operable controls. Stop cancels microphone and TTS.
  Mute cancels active TTS and suppresses subsequent automatic speech until
  unmuted. The explicit Read aloud control remains available only when unmuted.
- A click on the microphone while TTS is playing is a manual interruption:
  cancel TTS first, then listen. Do not describe this as acoustic barge-in.
- In-app `WakeWordAdapter` remains inert. Do not introduce closed-app listening,
  a wake-word vendor, browser SpeechRecognition wake polling, direct provider
  calls from TypeScript, new backend languages, actions, or approval logic.

## Worker ownership

Own only relevant TypeScript files under `packages/contracts/src/assistant.ts`,
`packages/assistant-runtime/src/voice.ts`, `packages/assistant-runtime/src/presentation.ts`
and their tests, `apps/web/src/lib/assistant-speech.ts`,
`apps/web/src/lib/assistant-controller.ts`, `apps/web/src/components/navox-assistant.tsx`,
`apps/web/src/components/navox-assistant.module.css`, and their tests. Small
related TypeScript consumer/validator edits are permitted when required for
compilation; list them in the report. Write the report to
`docs/agent-work/spec-008/M10-VOICE-WORKER-REPORT.md`. Do not edit checkpoint,
Python, migrations, provider configuration, lockfiles, CI, or SPEC-006/007 files.
The working tree is dirty with prior SPEC-008 work: preserve it. Other agents
may have edited the repository; never revert their changes.

## Acceptance

Test typed turn silent, voice turn speech, mute during active TTS, mute before
answer, unmute, Stop during listening and speaking, manual interruption during
speech, clear/unmount with late callbacks, unsupported microphone fallback, and
voice transcript containing `yes` not being treated as approval. Keep tests
meaningful and scoped to behavior. Run root npm lint, typecheck and tests plus
Web build; fix in-scope failures. Do not call paid providers, commit, push, or
deploy. Report exact checks and the remaining provider-backed voice gap.
