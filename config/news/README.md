# Reviewed public news source example

`public-sources.json` contains one narrowly scoped USGS earthquake feed. It is an
operator configuration example, not an automatically activated source.

USGS states that USGS-produced data may be reused with attribution:
https://www.usgs.gov/faqs/are-usgs-reportspublications-copyrighted

The endpoint is listed by its official Atom feed documentation:
https://earthquake.usgs.gov/earthquakes/feed/v1.0/atom.php

This review covers feed metadata and the permitted feed description only. Full
article processing/storage and image display remain denied. Third-party media
rights are not inferred from the USGS domain. Review expires on 2026-12-28; the
application fails closed afterward. Item retention is seven days, independent of
repeated poll observations. This is a public-source technical review, not a grant
for arbitrary publisher content.

A deployment operator can set `NEWS_SOURCE_CATALOG` to this JSON and explicitly
enable `NEWS_FEED_ENABLED`. An authenticated owner then adds the source. No news
feature is enabled by a migration. News AI additionally needs its new artifacts
published, news-specific evaluation, authorized routing and `NEWS_CHAT_ENABLED`;
old SPEC-005 qualifications do not qualify these tasks.
