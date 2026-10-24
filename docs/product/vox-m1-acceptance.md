# Vox milestone 1 acceptance: in progress

This branch is an implementation checkpoint, not a passed milestone. Production call/media/inference activation remains off. An isolated Vox test deployment is enabled; production application code and student data remain unchanged. Tests use synthetic data unless explicitly stated; none establishes phone behavior or real billed usage.

| User acceptance item | Current engineering evidence | Acceptance |
| --- | --- | --- |
| Tap Vox, real back-and-forth on phone browser | Live LiveKit/OpenAI browser calls answered two arithmetic turns correctly, including a follow-up and a multi-sentence prompt; forced primary outage also completed a real fallback call; deployed isolated HTTPS site | Waiting on physical-phone testing and recording |
| Interrupt mid-sentence | Live generated interruption accepted during active output after 6,382 ms of playout; next answer correctly returned fifteen; provider cancel/truncate and heard-prefix tests | Waiting on phone speaker/mic/Bluetooth latency and echo testing |
| Correct real classes and schedule | Live spoken query returned the saved QA course, 10:00 time and Lab 42; fixture meeting removed and course archived afterward | Waiting on spoken queries against the student's real account |
| Two-minute silence, local Hey NavoX, same conversation | Standby/wake lifecycle, supported local ASR, actual lazy Porcupine Web SDK, authenticated connection-bound configuration and private queued wake question | Not done: vendor-approved configuration/keyword/language assets, cold startup and question-onset behavior, phone wake and actual no-upload measurement |
| Lose/regain network without lost context | A browser-container network disconnect for six seconds triggered cleanup and replacement; an unrepeated follow-up correctly used the first answer after recovery | Waiting on physical-network/device testing. Expired call leases cannot be revived; a crash before checkpoint commit can lose the last turn |
| Hang up cleanly | Live browser end/close returned HTTP 204 and Call ended; final checks found no active calls, checkpoints, pending cleanup, Cloud rooms or participants | Waiting on phone microphone release and device checks |
| Per-call and daily per-student cost logging | Live calls logged speech/idle/connected milliseconds, provider tokens and known model cost; successful turns settled reservations. Live daily operator report returned matching totals; owner isolation covered by tests | Partial: LiveKit price and invoice reconciliation pending; unknown components remain explicitly unknown |

## Additional launch work still required

- Test proactive provider rollover at real provider limits with no repeated greeting, no lost speech onset and continuing audio output.
- Long context beyond the provider ceiling needs explicit compaction; current hard limit fails safely rather than silently dropping conversation.
- Expand the passed emulated terminal transport replacement to physical devices and longer outages, while preserving logout/reset/ownership revocation; see [the next continuity implementation boundary](vox-m1-continuity-next.md).
- Supply vendor-approved browser credential handling and licensed Web keyword/language models. Actual SDK and authenticated runtime configuration are implemented but remain disabled pending this setup and device qualification.
- The server-owned 80-percent notice is now shown once in the dialog or minimized dock. Qualify quota enforcement and the notice against actual paid calls; synthetic UI/status tests do not establish live enforcement.
- Select/test stock voices on real devices. Custom Noa voice follows milestone1.
- Isolated test link: https://vox-test.66.175.235.169.sslip.io/. The server-hosted browser accepts HTTPS; the connected Mac encountered a TLS protocol error for this hostname. Verify phone reachability. Credentials remain in the private local login file, never in this document.
- Instrument200-question voice corpus and the specified usage metrics; student/cohort thresholds remain Waiting on real usage.

## Approved scope decisions

Web-open calls only in this part; native locked-phone audio later. Noa artwork comes in Part2. Calendar connection/scheduling and persistent memory follow the user's milestone order. Configured Vox now keeps an encrypted temporary checkpoint of completed turns, bound to the owner, web session, call and generation. It expires with the renewable call lease and is purged on terminal call transitions/reset. Source Forget conservatively clears the entire active call because turns have no selective source provenance. This is active-call execution state, not saved memory/history; no raw audio is stored. Expiry cleanup keeps only a content-free tombstone until ordinary call pruning. Historical backups are not a forensic deletion guarantee. Wake question delivery is a short-lived encrypted work queue, erased on consumption/expiry/terminal cleanup, not saved conversation history.

## Review before activation

Retain baseline working student/account flows. Keep secrets outside Git and output. A draft code review or synthetic test pass is not permission to label acceptance passed. Publish acceptance results, phone recording supplied by Michael, and deviations before treating this milestone as complete.
