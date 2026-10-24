# 24 October redesign evidence

## Timezone correction — test release 6171872
- 2,528 web tests passed; TypeScript and Biome clean.
- 54 account/preferences API tests and 55 Vox schedule/notification tests passed.
- Real test API in Chromium with America/New_York device timezone: first-time
  detection persisted; auth, preferences, profile and timetable agreed.
- Today, Classes, Planner, Inbox and Settings headers matched local instants.
- Saving America/Los_Angeles updated the Settings header immediately; QA account
  restored to America/New_York after the check. No real student preference was edited.
- 375px browser emulation had no horizontal overflow; zero browser page errors.
- Unit clock cases cover New York 3 AM/noon/11 PM, West Coast date boundaries,
  05:00/12:00/17:00 greeting boundaries, spring-forward and repeated fall-back hour.
- timezone-mobile.png is evidence of the timezone release, NOT the final redesign.
- Physical phone tests, live Vox timezone answer and new visual matching remain pending.

## Open redesign evidence
First-time Today, normal Today, late-night Today and every other new screen still
need side-by-side review against the 19-page source. See ../../m2-redesign-20261024.md.
