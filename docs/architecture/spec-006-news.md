# SPEC-006: News and conversational intelligence

## Status and source

SPEC-006 is in progress. The foundation, story/evidence services, initial News UI
and owned conversation path are implemented. Full acceptance remains incomplete.
See [the acceptance record](../evidence/spec-006/README.md) for exact evidence and
remaining mandatory work; no milestone is declared complete by this design file.
The source is the owner's eleven-page `SPEC-006_NavoX_Complete.pdf`, SHA-256
`f0c23c289d01440059785494249bd9d4113f2fed746ed69ab6d6813615b3e27c`.
The complete specification includes News, X trends, news conversations, deeper
research, personalization, and simplification of Today and Subscriptions.

SPEC-005 supplies the policy-controlled AI gateway, task-bound evaluation,
owned sessions, validation and traces. News needs its own prompts, schemas and
evaluations before `NEWS_SYNTHESIS` can serve. Existing assistant or drafting
qualification is not evidence for news synthesis. News never executes email,
calendar or subscription actions.

## M1: authorized sources become canonical items

Build one vertical slice: registered source -> permission check -> bounded fetch
-> canonical NewsItem -> persisted attribution and provenance. Begin with
synthetic fixtures and disabled feeds, then use a small set of sources whose
current permissions have been reviewed. Feed availability is not a content
license. Source configuration, processing and display each enforce rights.

Proposed implementation locations follow the current repository boundaries:

| Area | Location and responsibility |
| --- | --- |
| Domain | `services/api/navox/news/`: typed contracts, rights decisions, normalization and ingestion service |
| Persistence | `services/api/navox/db/`: news source, versioned rights, feed and canonical item models registered with the existing Base |
| Migration | next revision under `services/api/migrations/versions/`, preserving upgrade/downgrade and model consistency |
| Fetch boundary | existing connector network and secret infrastructure; authorized endpoint and connection references, no credentials in news rows |
| Background work | existing Temporal worker conventions, bounded ingestion activity and idempotent receipts |
| API | existing authenticated workspace/user dependency, read-only item projection when the feed feature is enabled |
| Validation | offline rights/normalization tests, PostgreSQL boundary tests and bounded workflow integration fixtures |

Keep source, feed and item access scoped to the owning workspace in the first
implementation. Do not make licensed feeds or source credentials globally
readable to optimize public-feed caching. Reading history, saves, dismissals,
follows, preferences and conversations additionally belong to a specific user.

### Source and rights records

Sources carry name, verified domain, type, region, language, identity verification,
status and bounded metadata. Source types match the specification: publisher,
wire service, government, company, research, social, syndication and other.
Feeds carry an approved endpoint reference, type, category, region, language,
poll interval, health and last success/error times. Supported feed contracts are
RSS, Atom, API, licensed feed, webhook and social API; adapter support is explicit.
Disabled, unavailable or unsupported sources do not get silently substituted.

Use immutable rights versions with provenance of the permission decision and an
effective period. Each item binds the rights version used at ingestion. Current
revocation/expiry is also enforced at reads and subsequent processing; an old
snapshot does not preserve permission after revocation.

| Permission | Required enforcement point |
| --- | --- |
| `metadata_storage_allowed` | Before persisting headline, author, URL, timestamps or feed metadata |
| `snippet_storage_allowed` | Before storing or returning source descriptions/excerpts |
| `full_text_processing_allowed` | Before fetching full articles for processing or passing article text to extraction/AI |
| `full_text_storage_allowed` | Before any durable full-text payload, cache, retry artifact or log |
| `summary_generation_allowed` | Before requesting or storing a derived summary |
| `image_display_allowed` | Before returning media for display; source-level permission cannot override a third-party asset restriction |
| `attribution_required` / `link_required` | At every API/display projection and derived artifact |
| `retention_days` | At persistence and deletion/expiry scheduling, including dependent caches and snapshots |

Missing rights deny the operation. Do not infer full-text processing permission
from metadata or snippet storage. Fetch only the allowed representation and
exclude forbidden fields before durable storage. Required URLs must refer to
stored source records; models cannot supply new citation URLs. Keep raw articles,
credentials and model output out of ordinary telemetry.

### Canonical item and ingestion behavior

`NewsItem` includes source ID, external ID, source type, headline, canonical URL,
author, publication/update times, event start/end times, permitted description,
categories, language, region, retrieval time and rights profile ID. Preserve
event time separately from publication time. Unknown event time stays unknown;
the publication timestamp is not evidence of when an event occurred.

Normalize timestamps to aware UTC while preserving source provenance. Validate
source URLs through the existing network protections, including redirects; do
not fetch model-generated URLs. Keep external IDs scoped to their source and
workspace. Reprocessing the same source revision is idempotent, while a changed
source revision is retained for later correction/retraction processing.

Ingestion uses bounded payload sizes, request deadlines, source-specific rate
limits and retry budgets. A rights failure, malformed item or unsupported media
does not become a provider retry. Later source disablement stops scheduled
ingestion. No live source, provider route or feature is enabled by migrations.

### M1 completion evidence

- Source creation, permission provenance and source disablement are persisted.
- An allowed fixture becomes the expected canonical item with original URL,
  attribution, event/publication timestamps and rights-version binding.
- Denied metadata, snippets, full text, summary or media cannot reach the
  corresponding store, model call, cache or display projection.
- Revocation, expiry and retention apply to previously ingested items.
- Cross-workspace and cross-user requests cannot retrieve restricted data.
- Duplicate delivery is idempotent; corrections remain distinguishable.
- A bounded real authorized source observation supplements offline fixtures
  before claiming that M1 ingestion works in deployment.
- The complete local quality gate and all six exact-head hosted checks pass.

## Subsequent milestones

| Milestone | Required outcome |
| --- | --- |
| M2 Story intelligence | Exact deduplication and calibrated clustering; syndicated/circular reports do not inflate independent corroboration |
| M3 Verification | Claim/evidence graph, attributed interested-party claims, contradictions, corrections and retractions; application logic owns final state |
| M4 News experience | Clean category feed and story page, evidence-backed summaries and original source links |
| M5 X trends | Authorized X access; trending remains distinct from verified reporting |
| M6 Ask NavoX News | Global/story conversations, owned retrieval snapshots, fresh evidence and validated stored citations |
| M7 Deep intelligence | Bounded research, timelines, background, changes and documented coverage comparison |
| M8 Personalization | Explicit interests, follows and saves with scoped history; no inferred sensitive political identity |
| M9 Experience refresh | Shared components and simpler Today, Subscriptions and navigation |
| M10 Hardening | Rights, verification, security, accessibility, mobile behavior, evaluation and measured usability |

Use the specification's verification states: VERIFIED, CORROBORATED, ATTRIBUTED,
DEVELOPING, UNCONFIRMED, DISPUTED, CONTRADICTED and RETRACTED. A generated answer
cannot upgrade a claim's state. Propagate material corrections to story versions,
caches and conversations with visible history.

Feature flags start disabled: `news_feed`, `news_chat`, `x_trends`,
`deep_research`, `coverage_comparison`, `operational_relevance`, `today_refresh`
and `subscriptions_refresh`. Keep default cards to a headline, concise description,
status/source row and at most two prominent actions. Evidence/history belongs
behind progressive disclosure. Validate the proposed Today | NavoX | News |
Subscriptions navigation with actual usability tests.

## Acceptance gates carried forward unchanged

The source PDF's A-H demonstrations remain required: verified story, X rumor,
breaking updates, correction, news follow-up conversation, claim verification,
rights enforcement and consumer-facing language. Preserve failed and incomplete
observations alongside passes.

| Measurement | Required result |
| --- | --- |
| Exact deduplication precision | >=99% |
| Story clustering precision / recall | >=95% / >=90% |
| Material claim extraction precision | >=95% |
| Source attribution accuracy | >=99% |
| Independent-source counting | >=98% |
| Grounded News Chat claims / freshness compliance | >=98% / >=98% |
| Follow-up continuity | >=95% |
| Correction / retraction propagation | 100% / 100% |
| Fabricated URLs/citations or accepted unsupported definitive claims | 0 |
| Rights violations, cross-user leaks or unauthorized actions | 0 |
| Unassisted completion of core News tasks | >=90% |

Test circular sourcing, syndication, rumor, retraction, stale video, mismatched
headlines and prompt injection. Keyboard, screen reader, focus order, contrast,
text scaling, reduced motion and mobile touch behavior require actual validation;
unit tests or a green build alone do not establish these outcomes.
