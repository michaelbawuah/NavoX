# Reviewed public news sources

NavoX ships four credential-free, reviewed feeds. A signed-in owner can choose
and add these sources when `NEWS_PUBLIC_SOURCES_ENABLED=true` and the existing
`NEWS_FEED_ENABLED` gate is enabled. Source activation, storage, refresh, and
readback remain scoped to that owner and workspace. An owner never borrows another
owner's Perigon connection.

The canonical runtime catalog is `services/api/navox/news/public_sources.json`.
The compatible operator example at `config/news/public-sources.json` is kept in
sync with it by a fixture-backed consistency test. The API Dockerfile copies the
`navox` package, including this catalog. An
operator does not need to copy a JSON string into the environment. The production
compose overlay enables public templates for API, migrations, and workers through
their shared environment. It does not enable News serving, AI tasks, paid
providers, or any account's source automatically.

| Source | Topic | Item retention | Review expires |
| --- | --- | --- | --- |
| USGS significant earthquakes | Science and earthquakes | 7 days | 2026-12-28 UTC |
| NASA recent content | Space and science | 7 days | 2027-01-11 UTC |
| NOAA PMEL highlights | Oceans and climate research | 30 days | 2027-01-11 UTC |
| Bureau of Economic Analysis | Economy | 30 days | 2027-01-11 UTC |

These feeds provide headlines, permitted feed descriptions, publication times,
and direct source links. They are focused public information feeds, rather than a
licensed general world-news service. All require visible source attribution and a
source link. Full-article processing/storage and all images remain denied;
third-party photographs on a government website are not covered by this review.
Retention is measured from original publication, not reset by another poll.

NASA's current AI usage guidance requires outputs to be attributed to the AI
product rather than NASA and disallows attribution such as "according to NASA"
in AI products. Its catalog definition therefore denies generated summaries. The
News reader may show NASA's supplied feed text and links, with source attribution;
it must not present that text as NavoX-generated analysis. The other feeds permit
summary generation from the retained description, but that content permission
does not enable the separately gated News AI runtime or grant any provider route.

Each source has a bounded review window. Expired definitions disappear from the
source picker and cannot be activated or refreshed. The original USGS definition
and fingerprint are preserved. Operator definitions can still be supplied through
`NEWS_SOURCE_CATALOG`; duplicate operator keys and conflicting shipped public
keys fail closed instead of replacing a reviewed endpoint or widening rights.
An identical operator copy of a shipped definition is deduplicated, so enabling
public templates does not invalidate an existing USGS configuration.

## Primary references and endpoint verification

All four endpoints returned HTTP 200 and supported XML content types on
2026-10-13 UTC. The actual feed metadata was passed through NavoX's existing
bounded RSS/Atom parser; no article pages were fetched. Fixture tests preserve the
observed feed formats, including BEA's EDT timestamps and USGS Atom updated times,
without storing full feed bodies or articles.

- USGS: [Atom feed catalog](https://earthquake.usgs.gov/earthquakes/feed/v1.0/atom.php)
  and [copyright and attribution FAQ](https://www.usgs.gov/faqs/are-usgs-reportspublications-copyrighted).
  USGS-authored/produced information may be reused with attribution; non-USGS media
  may have separate copyrights.
- NASA: [official RSS catalog](https://www.nasa.gov/rss-feeds/) and
  [media and AI usage guidance](https://www.nasa.gov/nasa-brand-center/images-and-media/)
  (updated 2026-08-13). The general feed links to both `www.nasa.gov` and
  `science.nasa.gov`; its topical technology endpoint is not used because the
  observed content was broader than that topic.
- NOAA: [official PMEL feed catalog](https://www.pmel.noaa.gov/list-pmel-rss-feeds)
  and [NOAA copyright guidance](https://library.noaa.gov/blogs/news-research-highlights/question-of-the-quarter-noaacopyright)
  (2026-05-13). The feed uses PMEL's own highlights and only PMEL article links;
  the separate "In the News" feed, which can link to third-party reporting, is not
  included. The feed catalog's current Highlights link resolves to the endpoint
  ending in `rss-feed-pmel-highlights.xml`.
- BEA: [official website's News Release Feed link](https://www.bea.gov/)
  and [public-domain reuse FAQ](https://www.bea.gov/help/faq/147).
  Only the explicitly registered BEA article domains are accepted; malformed,
  relative, and off-domain feed links remain rejected.

No BBC, CNN, Perigon trial, publisher image, paid subscription, or API credential
is provisioned by this catalog. News AI still requires published artifacts,
news-specific evaluation, authorized routing, and its existing feature gate.
