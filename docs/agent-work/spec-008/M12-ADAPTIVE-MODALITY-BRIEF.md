# SPEC-008 M12 — adaptive response modality

## Outcome

Decide `TEXT`, `VOICE` or `BOTH` for each saved assistant response without granting action authority. Reuse the existing presentation plan, saved turns, SPEC-005 intent planner and M11D saved-turn TTS path. Keep the modality resolver, voice-session preference and shared contracts in TypeScript. Do not reimplement connected-service answers or provider access.

## Required behavior

- A typed question with Voice Mode off returns visual text and never starts TTS. A spoken question with Voice Mode on returns the same detailed visual blocks plus a bounded spoken answer (`BOTH`). Voice Mode and Mute are explicit, visible controls; Stop and Mute cancel pending synthesis/playback. Keep typed input usable when speech is unavailable.
- An explicit “read it to me” request resolves the most recent eligible answer **in the same authenticated AssistantSession**, creates a normal read-only turn or exact saved-turn selector, and speaks it through M11D. Do not interpret a transcript of “yes” or “send it” as approval. An ambiguous or ineligible referent asks for clarification and makes no provider call.
- An explicit “don't read it aloud” instruction suppresses automatic speech for that answer, even if the request arrived by voice. Handle natural variants through the existing planner/intent contract where possible; a small deterministic delivery-preference layer is acceptable if it does not become a grammar for general assistant capability routing. Preserve historical SPEC-005 prompt/schema revisions if planning contracts change.
- A long or compound answer keeps full visual detail and speaks a concise, coherent summary rather than slicing the first 600 characters mid-thought. The spoken text remains derived from the saved response, ≤600 characters, and contains no fabricated facts.
- Sensitive typed answers and approval/action content stay silent unless the user explicitly requests an eligible read aloud. Approval-required or non-ready turns remain unspeakable even on explicit request. The client never supplies speech text, model, voice, credential or action authority.

## Acceptance

Test typed/Voice Mode off, spoken/Voice Mode on, explicit speak/suppress cues, long compound response summary, sensitive typed silence, mute/Stop racing an in-flight TTS request, same-session referent selection, unknown/expired/approval-required referents and one coherent visual+spoken turn. Verify no extra provider call for a suppressed answer and no action from a spoken approval phrase. Run root JavaScript lint/typecheck/tests/Web build and focused SPEC-005 tests if the planner changes; root owns the full API pre-push gate, hosted exact-head CI and live acceptance. No paid call, operator-grant change, commit or push in this implementation bundle.
