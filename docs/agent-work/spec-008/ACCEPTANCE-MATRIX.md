# SPEC-008 R3 acceptance tracker

This tracks the PDF's mandatory scenarios against actual evidence. R3's
TypeScript stack and fast-track order apply; the owner's later removal of X
supersedes X portions of scenarios E and H. No scenario is accepted from a
contract or synthetic unit test alone.

| PDF scenario | Required observation | Planned phase | Status |
| --- | --- | --- | --- |
| A | Open-app, explicitly enabled Hands-Free wake and greeting | Voice/wake | In progress: local on-device adapter and saved greeting pass synthetic tests; real device and audible greeting remain |
| B | Today briefing by voice and visual detail in one session | M1 | In progress |
| C | Current-time Calendar/course answer and safe navigation | Other domains | In progress: direct current-time and next-class compound answer, Canvas/Calendar conflict explanation, and guarded Open Calendar/Open Class links pass locally; live-source answer/navigation remains |
| D | Fresh live weather without unnecessary model reasoning | Other domains | In progress: authenticated workspace weather adapter, freshness/city checks and local tests; live reading and latency observation remain |
| E | Fresh SPEC-006 trending explanation with verification distinction; no X | M6 | In progress: local read-only News route and attribution/verification tests; live feed observation remains |
| F | SPEC-004 renewal/access-end distinction or explicit unknown | M5 | In progress: local read-only answer and unknown/preview distinction; live provider observation remains |
| G | Authorized correct Gmail thread, ambiguity clarification | Connected/actions | In progress: exact message click selection is local; thread and live Gmail checks remain |
| H | Weather, class, Today and News multi-intent synthesis; no X | Other domains | In progress: all four read-only routes pass one synthetic scoped turn; live observation remains |
| I | News→Calendar→Gmail→draft→read aloud→exact send approval | Connected/actions and voice | Open |
| J | Ambiguous consequential target yields clarification and zero action | Connected/actions | In progress: synthetic email ambiguity/selection checks pass; live observation remains |
| K | Barge-in interrupts TTS and preserves conversation | Voice/wake | In progress: local acoustic test passes; real-device echo, interruption and continuity remain |
| L | Typed input with Voice off stays silent | M1 | In progress: local modality and suppression tests pass; live browser observation remains |
| M | Authorized referent navigation | Other domains | Open |
| N | Provider/source failure is qualified, never fabricated success | Every phase | Open |
| O | Activity reports only verified ledger outcomes | Voice/activity | Open |

Security corpus, 98% grounded answer target, 95% multi-intent/reference targets,
99% voice/text continuity, wake false-action zero and human accessibility remain
unmeasured. M1 can prove its bounded Today and typed/microphone path without
claiming these broader targets.
