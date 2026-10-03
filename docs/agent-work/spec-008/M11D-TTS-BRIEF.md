# SPEC-008 M11D — saved-turn provider speech

## Outcome

Complete `AssistantResponse → Response Modality Resolver → SPEC-005 TTS → speaker` for one saved assistant turn. The user hears the same bounded answer that `/navox` displays. A failed or unqualified speech provider leaves the visual answer usable. This is the next closure phase after M11C; acoustic barge-in, wake detection and adaptive modality remain separate acceptance phases.

## Contract and ownership

- Keep voice-session orchestration, playback, Next routes and shared contracts in TypeScript. The existing Python SPEC-005 service is the only provider credential and HTTP boundary; it already owns audio model qualification and may be extended for synthesis. Do not call a speech provider from the browser or Next directly.
- The browser supplies session and turn selectors only, never speech text, provider/model, voice, destination, grant or approval. The Next route proves current session ownership, locates the exact saved turn and derives bounded speech text from its saved presentation/answer. Do not synthesize a user question, action approval payload, unknown turn, stale turn or a turn requiring approval. A clicked Read aloud control may speak a saved typed answer; automatic speech follows the saved presentation and Voice Mode policy.
- Forward that bounded server-derived text to a fixed authenticated SPEC-005 synthesis endpoint. SPEC-005 must use the existing `SYNTHESIZE`/`SPEECH_SYNTHESIS` task and model qualification, SENSITIVE grants, exact published binding, audio-character cost reservation, quota, reauthorization after the provider call, bounded timeout and one fail-closed provider choice. It must not qualify a text model or infer eligibility from a key alone. Keep provider payload/body out of logs and errors.
- Start with one bounded MP3 response (`audio/mpeg`, ≤2 MiB) from the [official speech endpoint](https://developers.openai.com/api/reference/cli/resources/audio/subresources/speech/methods/create), with a fixed server voice. Validate the returned content type and byte bound. No persistent or URL-addressable audio. `Cache-Control: no-store` throughout. Stop, mute, clear, unmount or a newer turn aborts the request/playback and revokes any object URL.
- Keep the existing `SpeechAdapter` isolated so a later streaming/native adapter can replace the player. Retire browser `speechSynthesis` as the accepted path; never claim it as provider TTS. Typed text remains available if speaker/TTS is unavailable.

## Acceptance

With controlled SPEC-005 provider fakes, verify one saved voice answer becomes exactly one bounded provider synthesis and browser playback; typed Voice Mode off remains silent; explicit Read aloud can play a saved typed answer; wrong/cross-user session and turn selectors fail before upstream; unqualified/ungranted model, provider timeout/oversize/malformed audio fail closed without speech; and Stop/mute/clear/unmount suppress late playback. Assert that a voice transcript such as “yes” never authorizes an action. Run the repository's relevant TypeScript gates, API regression tests and full pre-push gate before commit/push; hosted CI must pass on the exact new head. No live paid call in this phase without reconciling the owner's remaining $10 session cap.
