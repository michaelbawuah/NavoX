# SPEC-007 explicit source identities and relationships

Status: Astra-approved implementation contract; no completion claim.
Root owns architecture/security acceptance. One Flash writer after News correction.
Original SPEC007 phase6/M4 requires useful supported entities, provenance and
RESOLVED/POSSIBLE_MATCH/AMBIGUOUS/DISTINCT resolution without merging names.
Existing graph only supports OTHER source nodes and PART_OF. Preserve that path.

## Architecture and security contract

Use existing source-controlled SourceDocument.author/recipients identities only,
plus exact Canvas course/assignment parent identity where the current adapter
provides it. Source assertions identify a mailbox/provider account; do not claim
verified human identity, infer organizations from email domains, infer sensitive
traits, or import unscoped legacy Person identity data. No model/provider calls.

A source entity is anchored to one knowledge resource and exact current revision.
Extend typed resource nodes for EMAIL, EVENT, COURSE, DOCUMENT as appropriate;
PERSON/account identity nodes may be source-scoped and carry only hashed canonical
identity in storage. Do not persist names, addresses, source bodies or credentials
in graph rows. Their display is rebuilt after current authority from the source.
Same exact explicit identity can resolve across currently authorized source anchors
owned by the same user; it never grants access or merges permission sets. Canonical
identity normalization must be conservative (no Gmail dot/plus stripping; opaque
provider IDs scoped by provider; email domain case-folding with explicit local-part
policy matching existing source normalization). Names alone never RESOLVED.

Persist bounded useful edges (author SENT email / ORGANIZED calendar event,
assignment BELONGS_TO source course) with evidence resource+version and valid time.
Useful account sharing across resources can be computed as a bounded one-hop
SAME_SOURCE_IDENTITY relation after verifying BOTH exact source assertions. Do not
invent persisted edges whose evidence references only one of two asserted facts.
All source/recipient inputs are untrusted data. Whitelist roles and identity kinds;
unknown/malformed inputs stay unavailable. Bound input and stored/read nodes,
expansion, source canonical reads and edges; report partial coverage when truncated.

Maintain source-scoped entity anchors so revoking one source neither leaks identity
nor destroys unrelated permitted evidence. Cleanup on delete/disconnect/revoke must
remove ALL entity/edge projections anchored to that resource (currently only the
knowledge:<resource> node is removed). Graph rows never authorize content reads.
Current account, current connector capability, source status, scope, exclusions,
current source identity metadata AND text/content revision must be revalidated
before source identity/title/body reads and again before publication. Immutable
snapshots, not comparison to refreshable ORM objects. If canonical identity metadata
changes without text hash change, old identity/edges must stop appearing immediately.
Do not add query-time source-content reads before permissions. No graph expansion
across owners even within one workspace. Preserve search global source/type/date
filters and one-hop bounds. Graph final fence must validate edge meaning, identity,
revision and both endpoints, not only edge IDs or row existence.

Expose meaningful resolution contracts (all four states). Exact same asserted key
is RESOLVED for source identity; different explicit keys are DISTINCT as source
identities, not a claim two real people cannot have aliases. Name-only candidate
is POSSIBLE_MATCH and multiple unresolved candidates AMBIGUOUS. Such candidates
must never expand retrieval as resolved. No public mutation/promote endpoint.
A read-only bounded resolution endpoint can compare source-anchored entity IDs;
responses cite permitted evidence and explain identity basis in application terms.
Do not change product flows to expose graph jargon.

## Integration and ownership

Worker owns knowledge/graph.py, new knowledge/entities.py or equivalent,
graph_retrieval.py, api/knowledge_graph.py, focused tests test_knowledge_entities.py,
existing graph tests, minimal lifecycle cleanup edits, additive contracts in
search_contracts.py and packages/contracts/src/knowledge.ts only if required.
No migration unless current schema cannot safely express anchors; report concrete
need to Astra first. Preserve existing root changes; you are not alone. No edits to
News/Ask/UI/runtime/provider/config/migrations without a specific dependency reason.
Update ENTITY-REPORT.md with exact behavior, tests and remaining gaps.

## Acceptance

- Two same-name/different-identity sources never resolve/expand together.
- Explicit same source identity resolves with provenance; email/calendar role
  relationships work, source-controlled course membership remains scoped.
- Ambiguous/name-only inputs never silently resolve; all states tested.
- Cross-user/workspace, deleted/revoked/excluded/capability-removed anchors hidden
  before source identity text reads, no denied names/counts/edges.
- Mid-read identity metadata mutation with unchanged body hash invalidates output;
  late authority changes invalidate graph-only search candidates and bundle edges.
- Lifecycle cleans all anchored identity nodes and edges with feature flag off;
  source return/reindex produces current valid anchors without duplicates.
- Bounded scanning/expansion, deterministic ordering, source/type/date filters.
- Existing graph/search/lifecycle tests and new identity cohort SQLite + disposable
  PostgreSQL, Ruff full owned paths, strict mypy. No paid calls or activation.
- Root owns final full API gate and combined exact-head acceptance.

Email thread structure: explicit same nonempty external_parent_id inside the same
owned connection can create a bounded SAME_THREAD resource relationship even when
no standalone thread resource exists. Both messages must retain current matching
thread identity and permissions; no title/subject threading. This is an independent
structural edge type, not proof of same author or same event. Preserve both messages
as evidence and recheck thread moves in graph publication. Add a regression for
same subject/different thread and thread move without body hash change.
