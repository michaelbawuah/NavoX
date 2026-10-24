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

## Welcome screen and persistent call — test release aef0bec
- 2,530 web tests; TypeScript and Biome passed.
- Real account UI: five setup steps, no fabricated completion; original supplied Noa SVG.
- Verified 18px header / 36px greeting, 220px sidebar and 56px header.
- 375px touch targets >=44px, no horizontal overflow, desktop/mobile light/dark,
  zero browser page errors. Screenshots use the existing synthetic Vox QA account.
- Live rented-provider call correctly answered that the saved class timezone is
  America/New_York/Eastern. Same document, same conversation and exactly one media
  admission through Classes -> Planner -> Today. Microphone tracks ended on hangup;
  active transcript became inaccessible (410) after the server acknowledged hangup.
- vox-navigation-mobile-emulation.webm records this flow. Authentication happened
  before recording; it contains no login credentials. Synthetic microphone input;
  video has no audio track. This is NOT a physical-phone recording.
- Review found primary CTA/mobile Search styling differences, corrected next.

## Deliberate limits / incomplete requirements
- Calendar helper copy points to Connections instead of claiming every connected
  calendar is busy-time-only: legacy permissions may include event read access.
- Full voice-driven class creation, all three calendar providers, Outlook email,
  Noa preference selection/interactive states, normal Today timeline/countdown,
  late-night alarm view and the other redesigned pages are not accepted yet.
- Noa on Today is the supplied static vector illustration. No simulated talking
  animation was introduced. Vox's existing visual remains pending Part 2.
- Physical-phone and real-student usage checks remain waiting on the user.
