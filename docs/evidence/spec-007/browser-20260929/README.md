# Rebuilt browser checks, 30 September 2026 02:32 UTC

Current isolated Next production build, real local Chrome with a new temporary profile,
and authored/intercepted API responses. All external network requests were blocked.
No owner account, live provider or connected data was used. This is frontend behavior
and visual evidence, not independent query quality or a human screen-reader study.

Seven interaction/layout checks passed: stable request ID after failed Ask; resource #2 follow-up;
clear history during a delayed answer; compact mobile Ask form; named owned-source outage; linked conflicting email/calendar dates with
explicit schedule authority; source refresh only after an explicit click. No page
errors. Ask and conflict views both320px wide at200%text, with scrollWidth320.
Root inspected both screenshots. The API equivalents run separately against isolated
PostgreSQL:38Ask tests pass,41conflict/graph/lifecycle/importance tests pass.

Browser harness: /tmp/navox-browser-ask-final-20260929.mjs. Log:
/tmp/navox-browser-ask-final.log. Captured report contains exact checks and dimensions.

30 September 2026: `live-refresh-report-20260930.json` and
`live-refresh-mobile-20260930.png` capture a real Chrome production-build flow
with synthetic intercepted API responses. One latest-query dispatch renders cached
coverage, one read-only poll updates the result, and 320px layout at 200% text
has no horizontal overflow. This is UI behavior evidence, not a live connector,
provider or human accessibility study.
