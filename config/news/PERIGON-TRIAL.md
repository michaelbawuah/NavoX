# Perigon trial News source

This opt-in profile supplies original publisher headlines, timestamps, bylines,
categories and links for AP, Reuters, BBC, CNN and CNBC through Perigon. It is a
SPEC-008 integration trial, not a production commercial content license.

The dated rights review uses [Perigon's Customer Subscription Agreement](https://perigon.io/CSA.pdf).
Its free tier permits individual use and evaluation of prospective commercial
applications. This profile stores metadata only for one day and expires on
2026-10-30. Publisher article bodies, snippets, images and Perigon-generated
summaries/verdicts are neither retained nor promoted into News claims.
Publisher links and attribution remain visible. The five feeds share one
conservative independence group and have no automatic verification authority.

## Setup

1. Use the approved configuration in `perigon-trial.example.json` as the
   private deployment's `GENERIC_REST_CONNECTORS`.
2. POST the owner's token to the existing authenticated
   `/api/v1/connectors/generic-rest-api/connect` flow, selecting the listed
   News read capabilities and exact consent. The existing SecretBroker encrypts
   and binds it to that user, workspace and connection. Never put the token in
   this file, source endpoints, request URLs, workflow history or Git.
3. Replace the source templates' zero UUID with the returned connection ID,
   review the license period, and set `NEWS_SOURCE_CATALOG` privately.
   Set `NEWS_FEED_ENABLED=true`. Other News/AI feature flags and provider grants
   are separate and are not enabled by this profile.
4. Activate the catalog sources with the existing News API, then refresh each
   once. The News workflow handles subsequent bounded ingestion, expiry and
   purge. News credentials are excluded from generic operational extraction
   and reconciliation; only the News reader uses them.
5. Verify current items, trending views and source links under the normal owner
   session. Failed credentials, revoked read access, pauses, changed approvals,
   cross-owner references and expired rights fail before publication.

Each feed requests at most 10 articles once per 24 hours. A free 150-request
monthly trial cannot support frequent real-time refresh across five feeds.
Initial and manual requests also consume its quota; exhaustion remains a
reported failure, never a reason to bypass the limit. Plan and license upgrades
are an explicit separate decision before ongoing public commercial use.

This enables attributed headlines. Named-topic narrative synthesis still
requires source content rights and its existing qualified AI route. Provider
headlines or popularity are not independent confirmation of their claims.
