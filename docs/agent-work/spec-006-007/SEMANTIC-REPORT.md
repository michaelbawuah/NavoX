# SPEC-007 S007-SEMANTIC checkpoint

STATUS: checkpoint / incomplete. This turn exhausted its context budget after
the required 0031 privacy corrections; the embedding service itself is not
implemented. No provider call, activation, commit or publication.

## Delivered (root's required 0031 corrections, unpublished migration)

All six requested corrections are applied to migration 0031 and mirrored in the
ORM models, with migration tests asserting the exact shape:

- `knowledge_sessions` now has a membership FK
  `(workspace_id, user_id) -> workspace_memberships(workspace_id, user_id)`
  (`fk_knowledge_sessions_membership`, CASCADE) plus
  `uq_knowledge_sessions_scope (id, workspace_id, user_id)`.
- `knowledge_turns` binds to its session with the composite FK
  `(session_id, workspace_id, user_id) -> knowledge_sessions(...)` (CASCADE),
  so a turn cannot point at another owner's session.
- The raw `answer` JSON column is removed; turns keep only ID/selector-shaped
  `selection` plus coverage/status metadata, so prose can never be persisted.
- `knowledge_relationships` endpoints are scoped with composite FKs
  `(from_entity_id, workspace_id)` and `(to_entity_id, workspace_id)` into
  `knowledge_entities(id, workspace_id)`, backed by the new
  `uq_knowledge_entities_scope (id, workspace_id)`.
- The vector namespace key now includes `dimension`
  (`resource_id, chunk_index, provider, model, registry_revision,
  embedding_version, dimension`), so vectors of different sizes can never be
  compared or overwrite each other.

Files: `services/api/migrations/versions/0031_knowledge_intelligence.py`,
`services/api/navox/db/knowledge.py`,
`services/api/tests/test_knowledge_migration.py`.

Verification: `pytest tests/test_knowledge_migration.py tests/test_knowledge_foundation.py`
-> 77 passed; `ruff check .` clean; `mypy navox` clean (254 files).
0029 and 0030 bytes are unchanged.

## Not implemented (the S007-SEMANTIC vertical slice)

1. `navox/knowledge/embeddings.py`: identifier-only durable attempt reservation
   (unique per user/request), hourly per-user quota and the <= $0.05 ceiling,
   gateway `EmbeddingInput` calls for document and query vectors via
   Profile.EMBEDDING / TaskType.EMBED / `knowledge_embedding@v1`, storage bound
   to workspace, resource, source hash, sensitivity, provider, model, registry
   revision, version, dimension and generated-at.
2. Fences: current VIEW, exclusions and source-revision checks before embedding
   any chunk (including `NO_CONTENT` and paused/disconnected sources) and again
   before use; discard on authority change during the call.
3. Query path: explicit `POST /search/semantic` (with `request_id`) that embeds
   the query alone, compares only inside the exact namespace, and fuses real
   retriever rankings through the existing RRF; GET search stays free and
   unchanged.
4. Honest coverage: no qualified/active embedding model, namespace mismatch or
   zero/NaN/Inf vectors return lexical and structured results with explicit
   incomplete-semantic reporting; no hash/random vectors.
5. Additive setting (default off) for the semantic path and its quota, plus the
   focused tests required by the brief: service + POST paths, revocation between
   reservation and use, namespace mismatch, private-vector exclusion,
   idempotent retry and quota exhaustion.

## Next steps for a fresh dispatch

Implement (1) reservation + gateway embedding call with injected fake adapters,
then (2) storage + fences, then (3) the POST route with namespace-exact
similarity and RRF fusion, then (4) the settings flag and the five focused test
groups above. Run the focused knowledge/provider suites plus lint and mypy;
defer the full combined gate to the complete intelligence integration as
agreed.
