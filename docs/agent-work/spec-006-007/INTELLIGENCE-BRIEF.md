# Next dependency phase: grounded knowledge intelligence

Prerequisite: Astra acceptance of S007-SEARCH and root embedding transport.
Authority: SEARCH-CONTRACT.md + original SPEC-007. Not a waiver of live gates.

## Concrete shared decisions

Gateway EmbeddingInput(text, purpose RETRIEVAL_QUERY/RETRIEVAL_DOCUMENT, optional
1..4096 dimensions) serializes directly as MinimizedContext.content. EMBEDDING is
knowledge_embedding@v1; Profile.EMBEDDING + TaskType.EMBED requires only EMBEDDINGS.
GatewayRuntime chooses and qualifies an explicitly registered model, enforces
provider policy, budget, trace and post-call reauthorization. Callers never call
HTTP adapters directly. max_output_tokens=1 is a budget bound; embedding providers
report zero output tokens. No default model/price/route is created.

Persist vectors with workspace, resource ID, source hash/version, sensitivity,
provider/model, registry revision, prompt/schema version, dimension and generated
at. No vector can enter similarity before current can_view/exclusions/version and
freshness checks. Query vector uses the same qualified gateway; compare ONLY the
exact same provider/model/registry-version/dimension namespace. Mismatched vectors
are omitted with incomplete semantic coverage, never mixed or silently projected.
Queries without an eligible provider still return permission-safe lexical and
structured results with explicit coverage. Zero/NaN/infinite vectors are rejected;
normalize with stable math. Result fusion consumes real retriever rankings via RRF.
Never use hash/random vectors or a fake natural-language synonym list as embeddings.
Index a bounded chunk/document through an identifier-only workflow or trusted
service. If authority changes during the call, discard the vector. Raw embedding
text cannot exceed 20k chars. Store no extra source body for embeddings.

## Grounded Ask and follow-ups

Versioned knowledge_answer@v1 prompt/schema artifacts are additive to the gateway.
Model output selects only supplied resource IDs + excerpt indices and structured
fact IDs, with insufficient_context. It cannot supply prose material claims, URLs,
authority, model routing or tool calls. Render selected exact evidence with source
attribution and uncertainty; do not imply that a source statement is verified fact.
This initial extractive answer is disclosed in evidence, not claimed as free-form
cross-source reasoning quality. Reject duplicate/foreign/out-of-range selections.
Provider context contains selected reauthorized EvidenceBundle only, never entire
accounts. Pre-call and post-call current authority/version checks are mandatory.

Persist owned knowledge_sessions, turns and ID-only retrieval snapshots. Snapshot
stores ranked displayed references, versions, methods, ranking version and freshness,
not copied titles/text/answers which can survive revocation. Read reconstructs safe
current output and withholds old output after any selected source's access/version
changes. Include user question and validated selection IDs with bounded retention.
Explicit follow-up #N resolves against immediately previous completed turn's displayed
results; reject invalid/ambiguous/stale referents. Ordinary history never grants view.
POST ask/query includes a request_id; reserve one provider attempt durably with unique
(session_id,request_id), per-user bounded hourly quota and a <=$0.05 task ceiling.
Retry returns existing owned turn/status, never buys another request. Missing provider
must produce honest unavailable/insufficient result with safe search results. Feature
knowledge_ask_enabled defaults false; tests use synthetic providers, no live calls.

## Graph and conflicts

Persist source-cited knowledge_entities and knowledge_relationships with explicit
scope, supported types, state RESOLVED/POSSIBLE_MATCH/AMBIGUOUS/DISTINCT, source
version and bounded validity. Names alone never merge identities. Initial supported
relationships may use exact source identities (email thread/message, assignment/course,
resource-parent folder) already present in authoritative canonical structure. No
speculative AI-generated graph. Graph traversal is bounded and reauthorizes evidence
resources and endpoint resources. Related/entity APIs return no inaccessible identity.

Conflicts require explicit same-entity/predicate facts with two permitted current
sources. Surface both values/times and link both. Assign Calendar/Canvas/subscriptions/
News authority from application adapter identity, never a model/content claim. Authority
labels do not delete conflicting communication evidence. Unknown entity identity
means no automatic merge or false conflict. Regression pairs include conflicting
meeting times and unrelated identically named events.

## Freshness/lifecycle/workflow scope

Use existing authorized source read dispatchers for on-demand refresh. No arbitrary
URLs, writes, tools, source grants or account selection. Bound refresh identifiers and
runtime; outages return qualified incomplete coverage. Source policies own freshness.
Provide the PDF's knowledge ingest/update/delete/embedding/permission refresh/reindex
identifier-only workflow boundaries registered with the worker; entity resolution may
remain deterministic exact identity. Deletion/revocation/disconnect is immediately
ineligible, then bounded async text/chunk/vector/graph cleanup. Do not let old events
resurrect deleted content or a newer revision. Excluding one user's shared resource
must not remove another authorized user's source; privacy filters apply at every read.

## Product and verification

Extend the accepted /navox page for Ask and sessions with progressive processing
feedback, exact source links, follow-up controls, and source outage/freshness notices.
No internal graph/vector scores. Implement PDF related/entity routes. Preserve current
Today/News/subscription navigation and existing action approval paths.

Offline end-to-end tests: connected search->bundle->provider selection->safe answer;
post-provider revocation/version change; forbidden high-similarity vector; embedding
namespace mismatch; removed-source old session; history clear separate from index;
followup ordering/idempotency; explicit conflict links and source authority; disabled
provider and outage incomplete status; graph evidence deletion; lifecycle cleanup
and stale event fences; prompt injection never changes routing or executes a tool.
Run existing unchanged full API gate and web/SDK tests/lint/types/build on final tree.
A-J demos must map to actual behavior, with unsupported/live/human gates left open.

## Integration clarifications after search review

Search phase needs SEARCH-REVIEW.md acceptance first; never build on its pre-review
permission shortcuts. Use Profile.ASSISTANT_INTERACTIVE + TaskType.REASON for Ask,
with the exact new knowledge_answer artifact's task qualification. Additive prompt
registration is allowed; changing existing prompt bytes, production assignments,
operator grants, or feature flags is not. Provider references, prices and route
identity always come from gateway trace, never connector fields/model output.

KnowledgeContext must implement gateway AuthorizedContext and re-read the exact
selected IDs+revisions in fresh DB sessions for every build; use the same validated
permission/exclusion/native-domain helpers as response publication. Reject credential-
like content via existing reject_credentials/configured_secrets before any call.
Sensitivity is the maximum trusted selected-resource sensitivity and PERSONAL for
user questions. Canonical/source/model text cannot lower it or select a provider.
Query embedding context is the user query alone with no hidden resource text; when
there is no eligible embedding model, do not make any paid attempt. Retrieved native
News needs SUMMARY rights at context assembly, original+current policies, catalog
fingerprint, current claim state and source availability exactly like NewsContext.

Callers may request true search in AUTO without purchasing an Ask call. Read-only GET
search must never buy a provider request merely because a URL was prefetched. Route
paid optional query embedding / Ask through explicit POST and the durable request ID /
quota reservation. Request/session IDs are not authority and must be strictly owned.
Feature-disabled sessions must remain private and cleanup must still run.

Final demos should execute real API/UI/service paths with authored fixtures and
fake qualified gateway adapters; label those synthetic. A bare helper unit test does
not establish Gmail+Drive+Calendar end-to-end. No qualified embedding model exists in
current live registry, no approved News source is configured, and no live connected
account test has been performed. Record those facts rather than inventing availability.

## Execution boundary 22:08 UTC

Search accepted locally after actual correction review plus 185 PostgreSQL cases,
262 News PostgreSQL cases and rebuilt browser date/race/exclusion/320px200% checks.
Root additionally fixed detail publication's moved-folder scope and added a regression
in service.py/test_knowledge_privacy.py (8 privacy tests pass; PG regression running).
Preserve it. Include root change in your final full gate; prior gate2305 predates it.

You own navox/knowledge, APIknowledge, db/knowledge, additive0031 migration, knowledge
contracts, webSearch/navox, knowledge tests, worker registration + minimal additive
connector dispatch hooks, Settings and additive AIprompt registration required here.
Do not modify News domain modules, root evidence or planning files. Root retains News
architecture and any later News implementation. No dependencies needed if existing
libraries suffice. Preserve all prior dirty changes. No paid calls or activation.

Browser tooling already works: isolated Next build /tmp/navox-browser-20260929 server
port49007; cachedPuppeteer script /tmp/navox-search-browser-20260929.mjs uses authored
APIinterception. You may author new fixture browser scripts; root can execute if your
sandbox cannot launchChrome. Do not run provider calls or owneraccount browser paths.
DisposablePG container remains127.0.0.1:54705; use NAVOX_CONNECTOR_TEST_DSN only with
isolated fixtures (tests/knowledge_search_support.py). Root can run final PG if denied.

Report docs/agent-work/spec-006-007/INTELLIGENCE-REPORT.md, exact changedfiles, tests,
remaining gaps and demo mapping. Finish coherent phase including actual service/API/
workflow/UI paths. If context gets tight preserve your implementation checkpoint and
continue; don't silently omit acceptance areas or substitute helper stubs for wiring.
