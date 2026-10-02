# News presentation checkpoint — 2026-10-02

## Product behavior

News now has a compact newsroom header, a lead story, three-column browsing
cards and a mobile layout. The photo and headline open the same owned story.
The story page puts the original publisher article link near the headline and
reuses the existing source-backed NavoX summary sections: what happened, why it
matters, what remains unclear, and latest development. Missing, changed or
unavailable summaries are stated plainly; old facts are not rendered as current.

Article photos use bounded URL/description/credit metadata. Their ingestion,
storage, story projection and subsequent readback require image display rights
and an operator-reviewed image host. Current and original rights are both
checked; narrowing permission hides and purges the image. Missing credits,
private or credential-bearing URLs, unapproved hosts and failed image loads
fall back to readable headline cards. The browser sends no referrer, and
Next's image optimizer does not fetch arbitrary publisher URLs on the server.

## Content boundary and actual deployment

The five Perigon trial sources remain metadata-only. No image, snippet,
full-text or summary permission was broadened, and the provider policy is
unchanged. Perigon supplies article URLs and headlines; its CSA does not confer
third-party publisher photo/content rights. Real article photos and detailed
summaries therefore remain unavailable for these live sources. A verified
content license and an already-qualified permitted summary route are required
before enabling them. No generated or stock images were substituted for news
photos, and no headline-only detailed narratives were fabricated.

Photo/summary browser fixtures were synthetic, isolated to an automated test
browser, and removed before returning to the live feed. No provider call or
user data mutation was used to fabricate that fixture. The live feed was
checked separately: 50 real headlines, zero photos, original article links,
and the accurate summary-unavailable state.

The update runs on the owner's existing Mac Compose integration at
http://localhost:3000/news. This is not a deployment to navox.net or a claim that
main/PR 22 has met its remaining acceptance criteria.

## Migration and verification

0035_news_article_images adds one nullable JSON column to news_items without
rewriting existing rows. Python None is stored as SQL NULL. Downgrade refuses
to discard populated image metadata. A separate disposable PostgreSQL database
proved row/digest preservation across downgrade/upgrade and refusal to drop
live image metadata. That database was removed afterwards. The owner's database
was backed up privately before applying the additive upgrade.

Local migrations, assistant migration, API, web and both workers were updated
through Compose. Web/live/readiness/owner News requests returned HTTP 200.
Both actual workflow queues had active pollers; News reconciliation remained
RUNNING. The current provider policy equals the pre-News policy.

The web/SDK lint and typechecks passed, with 391 runtime tests and 437 web tests
passing, plus the production web build. Focused API/migration checks passed.
The full locked API preflight passed: 2,791 tests, zero skips,
5 retained warnings; lint, format, strict mypy, schema checks, measured
safety metrics, deterministic release and patch whitespace all passed.

Hosted CI on the final commit must be checked separately. Existing acceptance
blockers (drafting human review/grant, verified controlled Gmail send, Canvas
credentials, public hosting/domain/OAuth, and real-device voice acceptance)
remain recorded; this UI checkpoint does not manufacture their completion.
