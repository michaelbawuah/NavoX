# Astra actual-patch review: semantic slice correction

Status not accepted. Good end-to-end shape and useful regression evidence; the
following concrete authority/payment defects must be corrected together.

1. **Stale/classification vectors are scored** in retrieval.semantic_retriever.
   Its vector SELECT filters only namespace+resource IDs, ignoring stored
   `source_content_hash` and `sensitivity`. A source can move and be reindexed to
   hashB while vectors from hashA continue to rank it. Filter current metadata
   before loading vector JSON: hash must equal current index AND canonical hash;
   sensitivity must equal current trusted classification; include current chunk
   existence/version. Recheck current authorization/exclusions before vector read.
   Tests: old vector + fresh new index, classification increase without hashchange.

2. **Authority helpers load content before permission**: embedding_authority and
   _document_context begin with select(KnowledgeResource) which includes indexed
   body/title. Use the existing metadata-only RESOURCE_AUTHORITY_COLUMNS pattern,
   adding only index/hash/classification fields. No fulltext/vector/chunk read
   before current VIEW. Bind source_connection_id/source_resource_id to the current
   knowledge row, not only the supplied binding. Add SQL projection privacy tests.

3. **Document spending breaches the one-attempt contract**: embed_resource loops
   up to20chunks, every task has max_cost=.05; it only stops when already-spent>=.05,
   so a second call can exceed the aggregate ceiling. Make each durable document
   request exactly ONE chunk/provider attempt with explicit bounded chunk_index
   (default0) bound into metadata reservation. Report actual total chunks and
   partial coverage; later workflows schedule identifier-only chunk jobs under the
   same global user quota. Do not invent batching the adapter doesn't support.
   Respect knowledge_enabled AND knowledge_semantic_enabled in document service
   and context, not only query route. No paid work through flags-off internal calls.

4. **Hourly quota races**: reserve_attempt counts then inserts different requestids
   without a user lock. Serialize quota reservation on the current User row (same
   lock order as existing News), check current membership/pause there, then count,
   insert+commit. PostgreSQL concurrent distinct request regression nearquota.
   Set created_at to the checked clock instead of mixing suppliednow/serverclock.

5. **Context isn't a fully bound authorization boundary**: KnowledgeContext emits
   cached request.text without re-reading the exact current chunk. After metadata
   authority, reload specified chunk and require exact content equals request.text
   (or build from fresh bound content). Require task sensitivity equals current
   request sensitivity, correct EMBEDDING prompt/schema/profile/type, no context
   references or documents/user_request, document binding mandatory/query binding
   absent with PERSONAL floor. Check flags each build. Reject altered chunk text,
   binding mismatch, sensitivity downgrade. Use actual current time at post-provider
   fences (not start-of-request `moment`) so a VIEW grant expiring midcall cannot
   publish; clock injection for tests is fine.

6. **Numerical validation isn't stable**: sqrt(sum(x*x)) overflows for1e308 and
   silently returns allzero normalized vectors; tiny values underflow. Use stable
   math.hypot/scaled norm with nonzero+finite checks. Stored vector parsing must
   reject bool/string/NaN/wronglength and return incomplete coverage, not ValueError
   or malformed numeric coercion. Test extreme finite vectors and corrupt JSON.

7. **Unknown cost reported as known .05**: document `spent` substitutes the ceiling
   for unknown cost then stores it in cost_micros. Preserve None for unknown cost;
   reserved ceiling is a separate constant/field. With exactly one attempt the
   aggregate accounting simplifies. Failed/discarded calls must retain knowncost
   if observed, and never label unknown zero.

8. **Coverage isn't honest for partial indexing**: one resource hasvectors and
   anotherauthorizedresource hasnone => currently no PARTIAL reason. Compare
   authorized current resources/chunks with usable current namespace coverage;
   missing vectors, outdatedvectors, captruncation => explicit partial. Bound vector
   rows loaded/scored (not unbounded per200resource IDs). Don't count private data.
   Add knowledge_embedding_requests to schema gate/CI/preflight table lists.

No full combined gate until this correction plus downstream intelligence phases.
Run focused regressions, lint/mypy; use disposablePostgreSQL for concurrency. Preserve
rootNews changes. Root fixed final explicit-review preservation there,15relation tests
pass and browser rebuiltNewsresearch checks pass. No published/live feature changes.

Root acceptance after correction: actual code review confirmed scope/hash/sensitivity
vector SQL fences, exact current chunk context, strict task bindings, single paid chunk
reservation, quota serialization, unknown-cost and coverage handling. Root strengthened
remaining numerical extremes with max-component scaling (4096finite1e308 dimensions),
oversized integer rejection, and forced fresh locked User/membership reads. Added
extreme regression; final local semantic36passed/1PG-onlyskipped. Worker PG225knowledge
cohort and quota race passed. Accepted locally; live model qualification remains absent.
