# S007-SEMANTIC execution report

STATUS: ready_for_review (correction cycle complete). Functional vertical slice:
durable one-attempt embedding reservations, one chunk per paid document request,
fenced storage, `POST /search/semantic`, honest coverage. No commit, push,
activation, migration application or provider call. Root News, graph and
lifecycle files untouched.

## Correction cycle for SEMANTIC-REVIEW.md

1. **Stale/classification vectors no longer reach the scorer.** The vector read
   joins `knowledge_resource_index`, `knowledge_resources` and `knowledge_chunks`
   and requires, in SQL, `source_content_hash == current index hash`,
   `sensitivity == current classification`, a non-deleted resource and a current
   chunk. Outdated rows are counted, never loaded as JSON, for coverage.
   Tests: a rebuilt projection at a new hash, and a classification raised without
   a hash change; both drop the vector and report `SEMANTIC_OUTDATED_VECTORS`.
2. **Authority helpers are metadata-only.** `embedding_authority` and
   `_document_context` project `RESOURCE_AUTHORITY_COLUMNS` plus sensitivity with
   `load_only`, and bind `source_connection_id` / `source_resource_id` to the
   current row instead of trusting the caller's binding. Test: with the grant
   revoked, an engine-level statement capture proves no `normalized_text`,
   `text_content` or `knowledge_embeddings` read happened.
3. **One chunk, one paid attempt, explicit index.** `embed_resource` takes
   `chunk_index: int = 0` (bounded by `MAX_CHUNK_INDEX`, also a DB check
   constraint) and makes exactly one provider call for exactly that chunk. The
   report carries `chunk_index`, the real `chunks_total`, and
   `SEMANTIC_PARTIAL_COVERAGE` when the resource has more chunks; later chunks are
   separate identifier-only requests under the same user quota. Both
   `knowledge_enabled` and `knowledge_semantic_enabled` are enforced in the
   document service and in every context build, so no internal call can buy work
   while a flag is off. Tests: a multi-chunk resource buys one call per request
   id, the next chunk is a new request, an out-of-bounds index is refused before
   reserving, and flags off raise `SemanticDisabled` with no gateway call.
4. **Quota reservation is serialized.** `reserve_attempt` locks the caller's
   `users` row first (same order as News), re-checks membership and pause, then
   counts the hourly window and inserts, writing `created_at` from the checked
   clock instead of mixing a supplied instant with the server default. Test: two
   concurrent distinct identifiers with quota 1 yield exactly one reservation on
   PostgreSQL (skipped on SQLite, which has no row locks).
5. **The context is a bound authorization boundary.** Every build verifies the
   embedding profile/task/prompt/schema/capability, empty documents, references
   and user request, exact task/request sensitivity equality, query bindings
   absent with a `PERSONAL` floor, document bindings mandatory, the feature
   flags, and the exact current chunk text (altered or rebuilt text is rejected).
   Fences run at the real current time per build; the service post-provider fence
   takes an injectable clock defaulting to `utc_now()`. Tests: tampered text,
   sensitivity downgrade, query-with-binding, document-without-binding, flags
   off, and a grant that expires mid-call producing `DISCARDED` with no vector.
6. **Numeric handling is stable.** `normalize_vector` uses `math.hypot`, so
   `1e308` inputs no longer overflow to a fake unit vector and subnormal inputs
   no longer collapse; `cosine_similarity` scales both sides to unit length
   before the dot product. `parse_stored_vector` rejects bools, strings, NaN,
   infinities and wrong dimensions instead of coercing them. Tests: extreme
   finite vectors normalize correctly and corrupt persisted rows produce
   incomplete coverage rather than an exception.
7. **Unknown cost stays unknown.** `cost_micros` records only the observed cost
   or `None`; failed and discarded calls keep a known observed cost, and the
   reserved `$0.05` ceiling stays a separate constant. Tests: unknown cost
   persists as `None`, and an invalid vector after a priced call keeps `777`
   rather than being replaced by `50_000`.
8. **Coverage reflects partial indexing.** `SemanticEvidence` carries `missing`,
   `outdated`, `unusable`, `namespace_other` and `vector_truncated` counts,
   computed only over the already-authorized candidate set so private rows never
   contribute, and the service maps them to `SEMANTIC_MISSING_VECTORS`,
   `SEMANTIC_OUTDATED_VECTORS`, `SEMANTIC_NAMESPACE_UNAVAILABLE` and
   `SEMANTIC_PARTIAL_COVERAGE`. Vector rows read and scored are capped by
   `SEMANTIC_VECTOR_BOUND`. Tests: one resource with a vector and one without
   reports `SEMANTIC_MISSING_VECTORS`. The new table is now in the schema gate
   (`scripts/check-api.sh`), the CI table list (`.github/workflows/ci.yml`) and
   `tests/test_api_preflight.py`.

## What the slice does

`navox/knowledge/embeddings.py` (new) is the paid path: `reserve_attempt` writes
one durable `knowledge_embedding_requests` row keyed
`(workspace_id, user_id, request_id)` under a user-row lock and commits before any
provider work; the per-user hourly quota counts those rows in SQL; the ceiling is
the code constant `SEMANTIC_MAX_COST = $0.05`. `RegisteredEmbeddingGateway` drives
`Profile.EMBEDDING` / `TaskType.EMBED` / `knowledge_embedding@v1` with
`allow_fallback=False`, asks `rank_eligible` before reserving, and returns `None`
instead of buying a fallback. `KnowledgeContext` is the gateway
`AuthorizedContext` described above. Vectors persist provider, model, the
`ai_task_runs` registry revision, `embedding_version = knowledge-embedding.v1`,
exact dimension, source content hash and sensitivity, are validated
finite/non-zero, and are stored normalized. `semantic_search` reserves once,
embeds the query alone (`purpose=RETRIEVAL_QUERY`, `PERSONAL`) and returns honest
lexical and structured results when no model qualifies, no vector exists, a
namespace mismatches, a stored row is unusable or coverage is partial.

`navox/knowledge/retrieval.py` adds `semantic_retriever`, `cosine_similarity` and
`parse_stored_vector`. Order of work: authority, exclusions and the canonical
revision fence, then classification/chunk/vector-revision currency in SQL, and
only then the vector JSON read, so a forbidden, moved, reclassified or rebuilt
resource is never scored. `navox/knowledge/service.py` fuses semantic ranks
through the existing RRF and the same `publication_check`, and reports coverage
through existing fields, so the response shape and web SDK contract are
unchanged. `POST /api/v1/search/semantic` requires the web origin and both flags,
mapping replay to 409, quota to 429, credential-like text to 422 and a lost
account to 403; `GET /search` never touches a gateway.

Lifecycle wiring signature for the owning phase:

```python
await embed_resource(
    database,
    workspace_id=..., user_id=..., resource_id=..., request_id=...,
    settings=..., chunk_index=0, gateway=None, now=None, clock=None,
) -> ResourceEmbeddingReport
```

One request identifier per chunk; the caller schedules chunk jobs.

## Changed paths

- `services/api/navox/knowledge/embeddings.py` (new)
- `services/api/navox/knowledge/retrieval.py`
- `services/api/navox/knowledge/service.py`
- `services/api/navox/knowledge/search_contracts.py`
- `services/api/navox/db/knowledge.py` (`KnowledgeEmbeddingRequest`)
- `services/api/migrations/versions/0031_knowledge_intelligence.py` (additive
  table, indexes and chunk bound; 0029/0030 bytes untouched)
- `services/api/navox/core/settings.py`
- `services/api/navox/api/knowledge.py`
- `services/api/tests/test_knowledge_semantic.py` (36 tests)
- `services/api/tests/test_knowledge_migration.py`
- `.github/workflows/ci.yml`, `scripts/check-api.sh`,
  `services/api/tests/test_api_preflight.py` (schema table list, one token each)

## Verification (final tree)

| Command | Result |
| --- | --- |
| `uv run pytest tests/test_knowledge_semantic.py -q` | 35 passed, 1 skipped (SQLite; the row-lock race needs PostgreSQL) |
| `uv run pytest tests/test_knowledge_semantic.py tests/test_knowledge_migration.py -q` with the disposable PostgreSQL DSN | 52 passed (36 + 16) |
| full `test_knowledge_*.py` cohort with the same DSN | 225 passed (137s) |
| focused cohort (all `test_knowledge_*.py` + `test_api_preflight.py` + AI gateway/provider/runtime/settings/registry/embeddings) | 490 passed, 1 skipped (SQLite) |
| `uv run ruff check .` | clean (includes root's new graph files) |
| `uv run ruff format --check <touched paths>` | clean |
| `uv run mypy navox` | clean (258 files) |

The PostgreSQL concurrency regression asserts that two simultaneous reservations
with distinct identifiers near the quota produce exactly one row. All provider
traffic in these tests is a synthetic adapter behind the real runtime. Full
`scripts/check-api.sh` remains deferred to the combined integration gate; no push
was attempted.

## Outstanding gaps and risks

- No qualified embedding model exists in the live registry and no live provider
  call was made; every gateway assertion here is synthetic.
- Document embedding has no scheduler yet; the lifecycle/workflow phase that owns
  it will call the signature above under the same user quota.
- Native News, commitments and subscriptions stay out of the semantic path:
  `STRUCTURED_RESOURCE_TYPES` keeps them out of `can_view_resource` and the vector
  read is restricted to authorized connected resources.
- Browser/visual QA is deferred with the full gate.

## Decisions for Astra to confirm

- Duplicate `request_id` returns 409 instead of replaying a stored result; no
  query vector or result body is persisted.
- Semantic coverage rides existing `partial_reasons` / `unavailable_modes`; there
  is no `SearchResponse` shape change.
- One chunk per request identifier is the paid contract; multi-chunk coverage is
  reported as partial until the later chunk jobs run.
- Unknown provider cost is persisted as `None`; the `$0.05` ceiling is a
  reservation bound, not an observed price.
