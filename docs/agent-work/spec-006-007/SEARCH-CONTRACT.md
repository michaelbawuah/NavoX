# Astra contract: connected retrieval and consumer experience

Authoritative scope: complete SPEC-007 PDF, not the earlier M1-only extract.
This contract sequences implementation; it does not waive any original completion gate.

## Architecture and authority decisions

1. The knowledge index is derived. ConnectorResource and current connector authority
   remain authoritative for connected resources. M1 can_view_resource is required
   before reading candidate content and again at evidence/response publication.
   A source version/hash change invalidates derived text, vectors, graph and sessions.
2. Source content is never permission, provider routing, endpoint, or tool authority.
   Trusted source-type/capability mappings reuse ai.context.context_capabilities.
   Deny unmapped/ambiguous mappings; do not let metadata choose a read capability.
3. Respect the existing retain_canonical_content decision. Canonical rows with
   content_persisted=False cannot be used to reconstruct or persist transient bodies.
   Projection consumes only already-retained canonical text; unavailable coverage is
   explicit. A future transient-live path must retain its existing consent boundary.
4. Knowledge feature flags default off. Enabling code in tests is not deployment.
   No embedding model/provider/prices/qualification are guessed or auto-enabled.
5. Only the trusted projection adapter may normalize an owned active connector's
   explicit current read grant into a USER VIEW permission for that connection owner.
   Never grant WORKSPACE/PUBLIC/GROUP automatically. Existing revoked grants must not
   be resurrected by a rebuild. Public HTTP accepts no grants or arbitrary content.
6. Native commitments/subscriptions/News use their existing owned domain services
   and freshness/rights semantics on every retrieval. They are separate typed
   EvidenceResource references (source_type + actual domain UUID), not fabricated
   ConnectorConnection rows or fake URLs. Native adapters may enter EvidenceBundle
   without being persisted in the connected-source KnowledgeResource table.
7. Search does not perform external actions. Refresh may dispatch only existing
   authorized read workflows with bounded identifiers; provider/model calls stay
   behind SPEC-005 budgets/qualification and explicit default-off feature settings.

## Stable public contracts

SearchRequest: query (trimmed, nonblank, <=2000 chars), mode AUTO/SEARCH/ASK;
filters sources (connection UUIDs), types (ResourceType), aware date_range; limit 1..50;
optional owned session_id. Unknown fields rejected; identifiers never imply grants.
AUTO chooses resource results for resource-like requests and ASK for questions.
Internal validated plan: intent FIND_RESOURCE/QUESTION_ANSWERING/ENTITY_LOOKUP/
TIMELINE/RELATIONSHIP/OPERATIONAL_STATE/AGGREGATION; modes FULLTEXT/STRUCTURED/
SEMANTIC/GRAPH; freshness CACHED/FRESH/LIVE_IF_NEEDED; bounded source/time filters.
A deterministic planner is acceptable as the initial application-owned baseline;
report its limitations without pretending semantic understanding.

EvidenceResource: resource_id, source_type, title, excerpts (exact source spans),
canonical_url (server provenance only, nullable), source_updated_at, source_version,
provenance. EvidenceBundle v1: query, resources, structured_facts, relationships,
freshness_summary, permission_snapshot_id, retrieval_trace_id. Scope is bound to
workspace/user internally, never accepted from the client as authority. Stable
provider-independent public schema is consumable by SPEC-008. IDs are unique in
(source_type, resource_id), so native and connected records cannot alias.

SearchResponse: interpreted_mode, results, answer (nullable), suggested_followups,
trace_id, session_id if used, coverage/freshness status. No raw vector or graph scores
in UI. Unavailable sources are reported only from user's own connection scope;
never reveal a denied resource's name, count, snippet or existence.

## Retrieval and indexing

Idempotent index by M1 identity. Validate source version; replace derived chunks,
embeddings and graph on content change; preserve original external parent/thread
identity and version. Store parser/ranking/embedding/entity versions honestly.
Source-aware chunks: bounded exact text spans with stable section identity;
calendar/assignments stay typed structured facts, not a prose-only surrogate.
Keyword retrieval searches permitted title/text/chunks; typed date filters operate
on actual due/event/renewal fields with explicit timezone, not textual guesses.
Candidate bounds must be documented and reflected as incomplete coverage. Avoid
loading the first N arbitrary resources and silently calling it global search.
Use stable database pagination and bounded runtime, with truthful truncation.

RRF deduplicates per-retriever ranks, k=60, deterministic ties; independent score
scales are not summed. Semantic path consumes finite dimension-checked vectors
with exact model/version binding, authorized before similarity and rechecked after.
No fabricated/hash vectors labelled as semantic. Missing qualified embeddings yields
an explicit unavailable mode with lexical/structured fallback.

Exclusions persist user/workspace SOURCE/FOLDER/RESOURCE/TYPE scope. Apply them before
reading/ranking content across all retrievers and when rereading history/sessions.
Use source-controlled external_parent_id for folder exclusion, never arbitrary
metadata permission assertions. History can be cleared independently of index.

## Graph, sessions, Ask, conflicts and lifecycle

Graph entities/relationships require evidence-resource provenance. Names alone
produce POSSIBLE_MATCH, never automatic identity merge. Only exact trusted source
identities or reviewed resolutions establish RESOLVED. Bound hops and neighbors;
each edge and endpoint needs current permitted evidence. No unsupported AI graph.

Sessions and turns belong to the authenticated user/workspace. Snapshot stores
candidate/selected IDs, source versions, modes/ranking version, freshness and ACL
snapshot metadata, not durable permission or copies of source text. Resolve '#2'
only against the immediately prior completed owned turn; reauthorize/refresh current
resource before inclusion. Never render an old answer after source revocation,
exclusion, deletion or version change. Bound session/query storage and provide clear.

Ask uses versioned SPEC-005 artifacts and minimized EvidenceBundle with exact
pre-call and post-call source/permission checks. Model selects exact evidence spans
and structured fact IDs; server renders attribution and canonical links. Unknown
IDs, invented URLs, changed versions, unsupported material claims and tool requests
are rejected. Model output cannot assign verification or overwrite domain facts.
Missing qualified provider returns an honest incomplete/unavailable answer plus
search results, never a fake generated answer. Single-attempt idempotency and
per-user budget/rate control required before a provider request.

Conflict detection uses explicit same-entity/predicate facts and provenance; do not
compare unrelated names or infer identities. Surface both values and source times.
Query-specific authority labels current Calendar for event time, Canvas for due
state, direct communication for what was said, SPEC-004 for subscription status,
SPEC-006 for News. Authority does not silently erase a conflicting statement.

Deletion/disconnect/revocation: immediate can_view denial; async bounded cleanup
removes text/chunks/vectors/graph for ineligible sources independent of serving flag.
No cache holds permanent grants. Reindex uses identifier-only workflows with current
source version fences; no old event can resurrect a removed/newer source.

## APIs/UI/acceptance

Implement PDF search/query/ask/resource/related/entity/recent routes with existing
auth/origin conventions. Exclusion and session routes may be added to support the
specified behavior. Use /navox Search/Ask page and shared SPEC-006 components, source
links, filters, recent search, followups, loading/status, retry/empty/outage states.
Read apps/web/AGENTS.md and installed Next documentation. Never expose implementation
jargon/scores. Keyboard, mobile, reduced-motion, focus and semantic structure matter.

Validation: behavioral permission/injection/version/freshness regressions, source
projections, no-content retention, RRF/no duplicate ranks, finite vector/version
rejection, session revocation, scoped exclusion, structured domain authority,
conflicts with both citations, unavailable coverage, history deletion, UI abort/race
handling. Full unchanged API and existing web/SDK/extension gates on final tree;
new tables added to schema lists plus corresponding fake-gate fixture; no weakened
thresholds. Retain an honest A-J and metric matrix; authored examples are regression
fixtures, not independently measured production recall or live qualification.
