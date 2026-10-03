# SPEC-008 M13 — in-app Hands-Free and acoustic interruption

**Status: local implementation; real-device acceptance pending.** This slice
continues the same authenticated AssistantSession and reuses the M11C SPEC-005
WAV transcription route and M11D saved-turn synthesis route. No new provider
access or action authority was added.

The `/navox` page has an explicit Hands-Free control and separate visible
`PREPARING`, `WAKE_LISTENING`, `CONVERSATION` and `PAUSED` states. A browser wake
adapter requires `SpeechRecognition.processLocally=true`, checks local language
availability and installs the en-US pack only after the control is enabled. It
never falls back to remote recognition. Wake detection stops when the page is
hidden, Hands-Free is disabled, the session is cleared or the component unmounts.
The exact wake phrase can include a bounded same-utterance question. That local
text enters the ordinary VOICE AssistantTurn as untrusted input. A bare wake
phrase creates a saved, read-only greeting turn; SPEC-005 TTS speaks it from
the saved-turn selector. A wake event cannot approve or execute an action.

During an active Hands-Free conversation, local speech activity automatically
finishes a bounded WAV clip after silence and sends it to the existing SPEC-005
transcription route. While NavoX speaks, an opt-in local microphone monitor
uses browser echo cancellation and a conservative speech threshold. Detected
speech cancels playback and continues the same clip into a new VOICE turn.
Thirty-second audio bounds, aborts, stale-callback checks, Mute, Stop, typed
input and the manual microphone remain. Ending the active conversation returns
to wake-listening; disabling Hands-Free releases wake and voice capture.

Local tests cover unsupported/on-device pack handling, no remote fallback,
exact and false wake phrases, one-utterance suffix, stop and restart races,
rapid browser recognizer endings, WAV auto-finish, echo-level non-trigger,
acoustic playback cancellation, same-session new turn, greeting with no planner
or source call, and wake state transitions. Browser unit tests do not establish
real microphone permission, pack installation, echo cancellation, acoustic
barge-in quality, device playback, or live TTS/STT/provider behavior. Those
remain mandatory end-to-end gates, along with connected-app/action acceptance.
