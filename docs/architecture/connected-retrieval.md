# Connected retrieval

Connected search combines a derived connector index with owned commitments,
subscriptions, and News. The canonical connector resource and each native domain
remain authoritative; a search result, stored session, or model response grants no access.

## Read and publication boundaries

[Permission checks](../../services/api/navox/knowledge/permissions.py) read current
membership, user pause state, resource deletion, explicit VIEW grants, connector
lifecycle, and effective read capabilities. Capabilities intersect the connector
manifest, provider capabilities, and the owner's authorization. Source text and
metadata cannot choose a capability or change these decisions.

[Retrieval](../../services/api/navox/knowledge/retrieval.py) first selects candidate
identities from scoped grants and checks current authority before text matching,
snippet loading, or vector scoring. Source, folder, resource, and type exclusions
apply alongside authorization. Denied content cannot affect matching counts.
The canonical content hash must still match the indexed hash before content is read.

Before returning results, [the search service](../../services/api/navox/knowledge/service.py)
checks the requesting account, reloads exclusions, rechecks connected authority and
hashes, and rereads native values through their owning services. Graph relationships
receive their own publication check. A further connected-resource check follows
conflict assembly; facts and conflicts are limited to surviving results.

## Index and lifecycle

[Indexing](../../services/api/navox/knowledge/indexing.py) projects only canonical
content already permitted for retention. Unretained bodies remain unavailable for
text search. The projection records source connection/resource identity, version,
hash, sensitivity, and source timestamps. Rebuilding does not restore revoked grants.
Text is bounded to 200,000 characters, with at most 200 paragraph-aware chunks of
1,200 characters each; typed calendar and due-date windows remain structured fields.

[Lifecycle processing](../../services/api/navox/knowledge/lifecycle.py) rechecks
current ownership and capability before projection. An event's expected hash must
match the canonical row, so delayed events cannot replace a newer revision.
Changed content or classification invalidates old vectors; graph anchors are rebuilt
from current source identities. Jobs carry identifiers rather than source bodies.

Permission loss clears derived text, titles, URLs, chunks, embeddings, and graph
anchors. Cleanup still runs with knowledge search disabled. Deletion or disconnect
retires the projection; a forced rebuild cannot resurrect it. Temporary authority
loss permits a later rebuild only after current authorization is restored.
These operations remove derived data, not data in the external source system.

## Retrieval modes and bounds

The [planner](../../services/api/navox/knowledge/planner.py) uses deterministic
surface cues, explicit filters, and at most 12 deduplicated terms. The request
[contracts](../../services/api/navox/knowledge/search_contracts.py) bound queries to
2,000 characters and pages to 1–50 results. Coverage reports truncation, stale drops,
unsearchable content, and unavailable sources rather than implying exhaustive recall.

| Path | Contract and current bounds |
| --- | --- |
| Lexical | Case-insensitive term matching over authorized titles and retained text; title hits score 3 and body hits 1. Connected candidate scans are bounded to 200 identities, chunk reads to 600, and excerpts to 3 per resource. |
| Structured | Trusted adapters supply typed event/due windows and domain facts. Date filters operate on those fields rather than dates guessed from prose. |
| Semantic | Cosine similarity compares only vectors with the same provider, model, registry revision, embedding version, and dimension. Nonfinite, zero, stale, or dimension-mismatched vectors are omitted. One request reads at most 5,000 vector rows. |
| Graph | One-hop expansion starts from at most 5 retrieved seeds and bounds expanded resource candidates to 100. Related-node reads scan at most 100 persisted edges and return at most 20 neighbors, with separately bounded identity lookup. |
| Fusion | [Reciprocal rank fusion](../../services/api/navox/knowledge/rrf.py) uses `k=60`, credits each key once per retriever, breaks ties by key, and retains at most 100 fused results before pagination. Independent score scales are never added. |

[Semantic requests](../../services/api/navox/knowledge/embeddings.py) reserve one
durable attempt before a provider call, enforce per-user hourly quotas and a $0.05
task ceiling, and recheck authority and revision afterward. Replaying a request ID
does not purchase another attempt. Missing qualified embeddings leave explicit
semantic coverage reasons and lexical/structured results; no substitute vectors are made.

[Graph identity](../../services/api/navox/knowledge/graph.py) uses canonical parent,
thread, and whitelisted source identity claims. A matching display name alone is a
possible match, not a resolved identity. Relationships cite the source resources
supporting them and revalidate both endpoints and revisions before publication.

**Availability:** these modes are implemented, but `knowledge_enabled`,
`knowledge_semantic_enabled`, and `knowledge_ask_enabled` default to false in
[settings](../../services/api/navox/core/settings.py). Operator configuration,
current source access, and exact gateway qualification determine which paths can
serve; enabling a feature flag alone does not authorize a provider request.

## Evidence, answers, and freshness

`EvidenceBundleV1` identifies resources by `(source_type, resource_id)` and carries
exact excerpts, source versions/timestamps, provenance, structured facts,
relationships, coverage, and trace/snapshot identifiers. Canonical URLs come from
source provenance. [Native adapters](../../services/api/navox/knowledge/adapters.py)
read commitments, subscriptions, and News using existing ownership, freshness, and
rights rules; they do not fabricate connector rows or persist duplicate domain content.

[Grounded Ask](../../services/api/navox/knowledge/ask.py) gives the gateway bounded
evidence and accepts validated excerpt/fact selections. The server renders citations;
the model cannot invent URLs, assign verification, or overwrite domain facts.
Owned session turns store selectors and bindings, and rereading reauthorizes their
sources. A revoked or changed source withholds the saved answer.

[Freshness](../../services/api/navox/knowledge/freshness.py) derives from canonical
fetch time: messages 15 minutes, Calendar 5 minutes, assignments/announcements
30 minutes, courses 6 hours, and documents/files 1 hour. Reindexing does not refresh
these clocks; unknown capabilities have no implied freshness window.
Ordinary search is cached. Explicit [live search and resource refresh](../../services/api/navox/api/knowledge_lifecycle.py)
reuse connector dispatch, ownership checks, cooldowns, and quotas. Live search selects
at most three relevant owned connection/capability targets and rereads results after
dispatch. Queued refresh is not reported as completed retrieval.

## Maintenance and regression coverage

[API routes](../../services/api/navox/api/knowledge.py) expose search, exclusions,
resource detail, and recent queries; [Ask routes](../../services/api/navox/api/knowledge_ask.py)
manage grounded answers and owned sessions. [Temporal workflows](../../services/api/navox/workflows/knowledge.py)
coordinate indexing, cleanup, reconciliation, and embedding work.

Start with [privacy](../../services/api/tests/test_knowledge_privacy.py),
[search](../../services/api/tests/test_knowledge_search.py),
[semantic](../../services/api/tests/test_knowledge_semantic.py),
[graph](../../services/api/tests/test_knowledge_graph.py),
[lifecycle](../../services/api/tests/test_knowledge_lifecycle.py), and
[Ask](../../services/api/tests/test_knowledge_ask.py) regressions when changing a boundary.
Run the repository [quality checks](../development/api-quality.md); synthetic cases
and passing tests do not measure production retrieval accuracy or live source availability.
