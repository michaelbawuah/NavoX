# Vox milestone 1 acceptance: in progress

This branch is an implementation checkpoint, not a passed milestone. Default call/media/inference activation remains off. Production navox.net has not been changed by this work. Tests use synthetic data unless explicitly stated; none establishes phone behavior or real billed usage.

| User acceptance item | Current engineering evidence | Acceptance |
| --- | --- | --- |
| Tap Vox, real back-and-forth on phone browser | Configurable persistent OpenAI realtime adapter, existing LiveKit call transport, cascade fallback, worker wiring; socket/worker tests | Not done: credentials, deployed end-to-end call and phone recording |
| Interrupt mid-sentence | Immediate local media gating, provider cancel/truncate, heard-prefix handling; synthetic tests | Not done: real speaker/mic/Bluetooth/network latency |
| Correct real classes and schedule | Read-only owner-scoped existing timetable/plan tool, server clock/student timezone, no guessed facts; timetable tests | Not done: spoken live queries against the student's own account |
| Two-minute silence, local Hey NavoX, same conversation | Standby/wake lifecycle, supported local ASR, optional Porcupine adapter seam and private queued wake question | Not done: licensed Web SDK wiring/configuration, keyword/language assets, phone wake and actual no-upload measurement |
| Lose/regain network without lost context | Bounded same-room SDK reconnect with account revalidation, unchanged worker/call; synthetic tests | Not done: real network test. Hard disconnect/worker-process death and outages beyond lease are not covered by RAM context |
| Hang up cleanly | Microphone, wake worker, playback, renewable epoch and durable provider cleanup fenced in tests | Not done: actual phone microphone release and LiveKit participant cleanup |
| Per-call and daily per-student cost logging | Server-owned ledger, modality counts, usage hooks, idempotency, versioned estimates, local-day/month totals, private operator API | Not done: live event/billing reconciliation; missing usage/prices remain explicitly unknown |

## Additional launch work still required

- Test proactive provider rollover at real provider limits with no repeated greeting, no lost speech onset and continuing audio output.
- Long context beyond the provider ceiling needs explicit compaction; current hard limit fails safely rather than silently dropping conversation.
- Restore conversation through terminal transport/worker replacement, while preserving logout/reset/ownership revocation.
- Configure the actual licensed wake SDK, authenticated runtime configuration and supplied Web models. The optional adapter alone does not enable phone wake.
- The server-owned 80-percent notice is now shown once in the dialog or minimized dock. Qualify quota enforcement and the notice against actual paid calls; synthetic UI/status tests do not establish live enforcement.
- Select/test stock voices on real devices. Custom Noa voice follows milestone1.
- Produce isolated test deployment/link after credentials are available and engineering gates pass; record emulation separately from physical-phone evidence.
- Instrument200-question voice corpus and the specified usage metrics; student/cohort thresholds remain Waiting on real usage.

## Approved scope decisions

Web-open calls only in this part; native locked-phone audio later. Noa artwork comes in Part2. Calendar connection/scheduling and persistent memory follow the user's milestone order. Temporary active-call context is RAM only until disclosed memory work; no raw audio or ordinary conversation history is saved by this checkpoint. Wake question delivery is a short-lived encrypted work queue, erased on consumption/expiry/terminal cleanup, not saved conversation history.

## Review before activation

Retain baseline working student/account flows. Keep secrets outside Git and output. A draft code review or synthetic test pass is not permission to label acceptance passed. Publish acceptance results, phone recording supplied by Michael, and deviations before treating this milestone as complete.
