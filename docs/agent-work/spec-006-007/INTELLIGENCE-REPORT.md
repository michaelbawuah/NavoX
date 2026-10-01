# SPEC-007 S007-INTELLIGENCE checkpoint

STATUS: checkpoint / incomplete (NOT ready for review as a phase; this turn ran
out of context budget). No provider call, activation, commit or publication.
Tree fingerprint at checkpoint: `2fcb565f5914721ab02c8f8090f48315abb6545e3cffe887915e341a42f4db75`.
0030 bytes unchanged (`244e2e17…`); 0029 untouched.

## Implemented and gated in this turn

- `services/api/migrations/versions/0031_knowledge_intelligence.py` (additive,
  `down_revision = "0030_knowledge_search"`): `knowledge_embeddings` (per
  resource/chunk with source hash, sensitivity, provider/model/registry
  revision/embedding version, dimension 1..4096, JSON vector, unique namespace
  constraint and cascade scope FK), `knowledge_sessions`, `knowledge_turns`
  (unique `(session_id, request_id)` and `(session_id, sequence)`, status check),
  `knowledge_entities` (unique workspace/type/canonical key, state check) and
  `knowledge_relationships` (unique edge, entity FKs, state check).
- `services/api/navox/db/knowledge.py`: matching ORM classes
  `KnowledgeEmbedding`, `KnowledgeSession`, `KnowledgeTurn`, `KnowledgeEntity`,
  `KnowledgeRelationship`.
- `services/api/tests/test_knowledge_migration.py`: 0031 is now the single head;
  new assertions compare every 0031 table's columns, nullability, unique
  constraints, checks, foreign keys and indexes against ORM metadata, verify the
  vector dimension bound and cascade scope, and confirm downgrade drops only its
  own tables. 16 migration tests pass.
- Schema lists updated in `scripts/check-api.sh`, `.github/workflows/ci.yml` and
  `services/api/tests/test_api_preflight.py` for the five new tables.

Verification on this tree: `ruff check .` clean, `mypy navox` clean (253 files),
migration + API + preflight focused tests `43 passed`, and the unchanged full
gate `bash scripts/check-api.sh` → 11/11 gates, `2309 passed, 4 warnings in
210.09s`, exit 0 (`/private/tmp/navox-intel-gate.log`).

This layer is inert: no route, workflow or service reads or writes these tables
yet, so no vectors exist and no provider request can be made.

## Not implemented (all remaining acceptance areas)

Nothing else from INTELLIGENCE-BRIEF.md is started. Specifically outstanding:

1. Embedding generation through the SPEC-005 gateway (Profile.EMBEDDING /
   TaskType.EMBED / `knowledge_embedding@v1`), namespace-exact similarity and
   fusion into the existing RRF path, with incomplete-coverage reporting when no
   eligible model exists.
2. Grounded Ask: `knowledge_answer@v1` prompt/schema artifacts,
   `KnowledgeContext` as a gateway `AuthorizedContext` with fresh per-build
   re-reads, extractive selection validation, `POST` ask route with durable
   `(session_id, request_id)` reservation, per-user hourly quota and the $0.05
   ceiling, and honest unavailable results with lexical fallback.
3. Session and turn services: owned session listing/clearing, ID-only snapshots,
   follow-up `#N` resolution against the previous completed turn, and withholding
   old output after revocation or version change.
4. Graph and conflicts: deterministic exact-identity relationships from canonical
   parent structure, bounded reauthorizing traversal, entity/related routes, and
   explicit same-entity/predicate conflict surfacing with authority labels.
5. Lifecycle: identifier-only refresh/reindex/delete/embedding/permission-refresh
   workflow boundaries registered with the worker, plus bounded cleanup of text,
   chunks, vectors and graph on revocation or disconnect.
6. Consumer UI: Ask and session surfaces on `/navox/search`, follow-up controls
   and outage/freshness notices.
7. Additive settings (`knowledge_ask_enabled`, embedding/ask gates) and the
   offline end-to-end tests listed in the brief (provider selection, post-call
   revocation, forbidden high-similarity vector, namespace mismatch, removed
   source in an old session, follow-up ordering/idempotency, conflicts, outage,
   graph deletion, lifecycle fences, prompt injection).

## Next steps for a fresh dispatch

Implement in dependency order: (1) embedding contracts + gateway call + stored
vectors + similarity fusion with tests; (2) Ask prompt/schema + KnowledgeContext
+ reservation/quota + sessions/turns; (3) graph/conflicts; (4) lifecycle workflow
registration and cleanup; (5) UI. Keep 0029/0030 bytes fixed, keep News domain
and root files untouched, and re-run the unchanged API gate plus web
lint/typecheck/test/build on the final tree.
