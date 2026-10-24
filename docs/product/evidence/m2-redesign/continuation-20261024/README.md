# Milestone 2 continuation — current evidence

Runtime code: **`c18329ee4eef35c48dac1a072926efee0d3843f7`**

Isolated test site: https://vox-test.66-175-235-169.nip.io/

Draft PR: https://github.com/michaelbawuah/NavoX/pull/57

Open [index.html](index.html) locally for the interactive reference comparison.
It switches between desktop/375-pixel and dark/light views. Image links open
full-resolution captures. GitHub displays the HTML source; download the folder
or open the checked-out file to use the comparison controls.

The [current milestone ledger](../../../m2-redesign-20261024.md) records the
implementation, scope decisions and remaining acceptance. Provider setup and
live cases are in [provider qualification](../../../m2-provider-qualification.md).

## Verified evidence

| Evidence | Result and scope |
| --- | --- |
| [Comparison viewer](comparison-index-checks.json) | 12 reference comparisons load in both viewer widths and all size/theme selections; 24 images per selection, no page errors or overflow. Video metadata verified. |
| [Deployment](deployment-checks.json) | Test stack built and migrated through 0061; ready. Public production unchanged. |
| [Engineering checks](engineering-checks.json) | 2,740 frontend tests, 45 final Vox API tests, TypeScript, Ruff and mypy passed. Biome: zero errors, 32 warnings, 14 informational findings. Earlier 139 focused Inbox/planning API cases retain their revision. |
| [Live browser layouts](browser-checks.json) | 48 saved/fresh page views: six routes, desktop/mobile, dark/light. No runtime errors or horizontal page overflow. |
| [Today clocks](today-controlled-clock-checks.json) | 12 captures at 9 AM, 3 PM and 3 AM in the saved New York timezone, with real saved synthetic data and a controlled display clock. Night starts collapsed and can expand. The QA alarm is disabled in these shots. |
| [Five setup entries](five-step-entry-checks.json) | All five destinations and the anonymous entry passed. Saved zone verified with a Honolulu browser zone; fresh saved data unchanged. Provider consent and successful Vox standby were not asserted by this read-only run. |
| [Connected Inbox fixtures](inbox-route-fixtures/fixture-scope-and-results.json) | 32 visibly labelled route-fixture captures. Source review, actual fixture conflict, two alternatives and copied instructions, exact saved draft and disabled unapproved Send checked. No real mailbox, OAuth, provider call, fake database connection or external send. |
| [Live Vox call](vox-capture-browser.json) | Real provider with typed requests and synthetic microphone; both complete turn checkpoints, task review, same-page class handoff, three routes, one admission, unchanged full saved records, clean hangup and transcript 410. |
| [Vox recording](vox-review-navigation-mobile-emulation.webm) | Video recorded with a 375-pixel browser viewport. It is visual evidence, not physical microphone/speaker or interruption acceptance. |
| [Broad Linux/PostgreSQL run](linux-postgres-qualification.json) | 247 backend tests passed on `3244a91`, with synthetic provider responses. |
| [Final-image Vox Linux run](linux-vox-qualification.json) | 45 Vox tests passed on the final `c18329e` image. Both qualification databases were removed. |
| [Qualification scope](backend-qualification-scope.json) | Lists the five Vox source/test files changed after the broad backend run. Outlook, planning, privacy, schema and runtime dependencies were unchanged. Results are not relabelled as if all tests ran on one revision. |

## Design references and visual limits

Reference images were extracted from the supplied 19-page October 24 PDF.
[reference-screens.json](reference-screens.json) records page numbers, dimensions
and the PDF digest. The references are unchanged; implementation images use
clearly synthetic QA names, courses, tasks and events.

The written 90-minute block cap takes precedence over the two-hour sample
blocks. The mobile timetable scrolls across the week and keeps hour labels
visible. Connected Inbox adds a local **Sent replies** view for verified replied
receipts; it is not the provider's full Sent folder. Full Settings/privacy/history
visual parity is deferred to M5 under the supplied specification. Full-page
captures show fixed navigation once; viewport captures show the actual visible
area and keep the checked meeting alternatives above the fixed controls.

## Vox failure and correction

The initial live task review succeeded before its spoken turn exceeded the
30-second audio bound. The earlier failures are retained under
`diagnostics-3244a91` and `diagnostics-74711c0`; they are not successful calls.
Content-free diagnostics identified the audio-bound rejection. The final task
handoff supplies the count of affected blocks to the model and requests a short
spoken summary. The complete screen review and approval hash remain intact.
The final recording waits for both exact completed transcript checkpoints before
navigating. The audio limit and no-replay rules were not relaxed.

## Acceptance still pending

Google and Microsoft OAuth application credentials are unconfigured in this
deployment. Genuine consent, mailbox/calendar reads, controlled approved sends,
exact sent receipts and Microsoft reply tracking remain live qualification
gates. No external email was sent in this continuation.

Physical-phone acceptance and real student usage remain open. Closed/locked-phone
ringing and snooze are native work. Actual elapsed focus, freezes and rewards are
Noa Part 2; the current streak credits recorded completed blocks. Full memory,
history and privacy redesign acceptance belongs to M5. No main/production merge
or milestone acceptance is implied by these checks.

Later commits containing only this ledger and evidence do not change the runtime
code revision stated above.

## Reusable evidence helpers

The `qa/` folder keeps the route-fixture capture, read-only five-step entry check
and comparison-index generator. The five-step helper requires `NAVOX_QA_CREDENTIALS`
to name a private JSON file; credentials and browser storage state are never
committed. Set `NAVOX_QA_RELEASE` to the runtime revision and provide Playwright
through `NODE_PATH` or `NAVOX_PLAYWRIGHT_MODULE`. Fixture captures require no login.
