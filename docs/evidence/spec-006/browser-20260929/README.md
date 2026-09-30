# News browser check, 29 September 2026

Executed isolated headless Chrome against the built Next app copied to `/tmp`.
All API requests were intercepted with freshly authored fixtures. External source
links were inspected, not opened. No owner browser profile/account/provider call
was used. This is synthetic UI evidence, not a human study or live ingestion test.

Passed Trending feed selection and its activity qualification, Save/refreshed state,
server-owned global question intent (intent omitted), numbered follow-up input +
second answer, exact fixture source link, visible keyboard skip link, mobile width
390/320 and accessibility-tree capture. Browser tested reduced-motion media; no
animation compliance or full screen-reader test is inferred from that alone.

Found real 200%-font overflow: document width395 on viewport320. Cause: shared
header intrinsic minimum width and preference grid/fieldset minimum sizing.
Proposed CSS in `navox-ui.module.css` and `news-workspace.module.css` reduced the
same case to320 with no overflowing element. The proposed fix was injected into
the frozen old build for this measurement. **A final rebuilt-app rerun is pending.**
A form-control scan found five 19px checkboxes; their wrapping labels already have
44px minimum height. The scan alone is not a touch-target failure or certification.

The initial harness run failed by reading OPTIONS as a POST body; corrected to
select actual POST requests, then passed. A preliminary scan recorded text overflow
without failing; the corrected check now asserts text-scaling width explicitly.
No product failure was hidden by those harness corrections.

`proposed-css-check.json` contains synthetic checks and accessible control names;
PNG files show mobile and scaled proposed appearance. No production-quality or
>=90% human task-completion claim follows from these artifacts.

Cross-product synthetic browser check also exercised Today priority + upcoming meeting
and Subscriptions service + known monthly cost. Both fit at320/390 normally and emitted
no page errors. At200% font, a clipped Today status/control form and subscription
heading/button were found (page overflow:hidden had masked the issue). Proposed
source CSS fixes now constrain intrinsic minima, wrap control headings and buttons,
and keep input grids shrinkable. The same browser scan with the capture form opened
shows no content extending beyond320px; only intentionally clipped decorative glow
elements do. `cross-product-proposed-check.json` records these results. No send,
cancellation, or account-change request was executed. Final rebuild check pending.

Four source CSS files changed: shared navox-ui, News workspace, Today workspace,
Subscriptions dashboard. Targeted Biome checks passed; nine existing specificity
warnings remain in the subscription stylesheet. No test gate was loosened.

## Compiled-build verification

The worker's corrected production build was copied to the isolated server and both
browser scripts reran **without injected layout fixes**. All listed News interactions,
keyboard skip and 320/390/200%-text checks pass. Today priority/meeting and subscription
cost displays render without page errors; opened capture form and subscription content
fit at200% font/320px. Only decorative Today glow bounds extend outside the viewport.
`compiled-news-check.json` and `compiled-cross-product-check.json` are the rebuilt
results. This closes the pending rebuild check for these four CSS files, not a human
usability/screen-reader gate or the entire SPEC-006 acceptance.

Final new News research controls: `research-final-check.json` and
`research-text200.png`. Compiled app, authored intercepted API, no provider calls.
Timeline focus sends TIMELINE while leaving freshness to the server; retained
source event/publication times include year and time zone. Existing trending,
save/follow-up/exact-source-link, keyboard skip,390/320px and320px200% text checks
passed. An initial harness Save click hit a detached node during React feed refresh;
a locator waited for stability and the rerun passed. Checkbox input boxes remain
19px inside labelled click areas; this is not a human accessibility certification.
