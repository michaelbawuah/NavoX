# S007-ASK execution report (correction cycle)

STATUS: ready_for_review. Grounded Ask implemented end to end, then corrected for
all eight ASK-REVIEW groups. No live provider call, activation, commit or push.
Root graph/lifecycle/conflict files preserved; the only shared file touched is
`packages/contracts/src/knowledge.ts` (Ask types) and the two Ask-mode lines in
`search-workspace.tsx` that root is now mounting over.

## Corrections for ASK-REVIEW.md

1. **Strong revision and exact excerpt binding.** `AskCandidate` now carries the
   application-validated canonical `content_hash`, connection/resource identity,
   classification, title digest, source version and exact excerpt locators
   (`(chunk_index, start, end, sha256(text))`). `AskContext.build` rebuilds current
   evidence in a fresh session, rebuilds candidates and requires an identical
   `binding()` for every candidate (hash, scope, identity, sensitivity, title
   digest, version, locator digests, fact values) before it serializes the
   *fresh* payload. Nullable `source_version` can no longer carry stale text.
2. **Every response path re-fences all displayed evidence.**
   `bound_evidence_for_ids` runs authority/exclusions/revision/index-state before
   the text read and `publication_check` again after it; every response branch
   (ready, withheld, incomplete, insufficient, unavailable) rebuilds `results`
   from `_results_now(...)` at the real current time. A non-selected displayed
   result that is revoked mid-call disappears from the response while the
   selected citation still renders. Session/list reads re-check membership and
   the API maps `KnowledgeUnavailable` to 403 instead of 500.
3. **No model prose.** `uncertainty` was removed from `KnowledgeAnswer`,
   `AskResponse`, `AskTurnView`, snapshots and the UI; answers carry
   application-owned reason codes only. Duplicate excerpt indices and duplicate
   fact ids are rejected by the schema.
4. **Durable attempt/turn concurrency and early idempotency.** `find_attempt`
   runs before session creation, eligibility or any provider work, so a known
   identifier replays its owned turn even after qualification disappears or
   history changes. `reserve_ask` re-reads the user row under `FOR UPDATE` with
   `populate_existing`, re-checks membership/pause, and only then counts the
   window. `reserve_turn` locks the session row, computes `max(sequence)+1` and
   enforces the turn bound *before* the provider call, committing a RESERVED turn
   so no lock is held across the call. Free search turns are idempotent per
   `(session, request_id)`. Clearing history during the call is detected by
   re-reading the turn, and the response is withheld instead of restored.
5. **Follow-up contract.** `#N` now means the Nth **displayed resource** of the
   immediately previous completed turn (not the Nth turn). The displayed order is
   stored separately as `selection.displayed`; `#N` is parsed from the question
   or the referent, conflicting/ambiguous/out-of-range/missing referents raise
   before any provider work, `followup_of` must be the previous completed turn,
   and a stale focus fails closed instead of falling back to a broad search. The
   web panel renders numbered source controls bound to that order.
6. **Retention and privacy erase.** `purge_expired_history(database, *, now=None,
   retention=30 days)` deletes owned sessions/turns and is safe with every flag
   off; paid reservations keep identifiers only, so quota and replay survive.
   New-session creation is bounded (`MAX_SESSIONS`, oldest pruned). Free search
   turns reject credential-like queries. `DELETE /knowledge/sessions[...]` stays
   available when the Ask/search flags are off.
7. **UI races and paid retry identifiers.** `SubmitLedger` keeps one paid request
   id per pending `(question, session, referent)`; the id is reused on a timeout
   retry and resolved only on success, and forgotten when the input changes or
   history is cleared. Clear/delete invalidate both request gates, erase local
   answer/history first, and follow-up opening is fenced and error-handled.
8. **Real gateway verification.** New tests drive `RegisteredAskGateway` through
   the real runtime with a qualified registry fixture, `knowledge_answer@v1`
   artifact, synthetic HTTP adapter and trace assertions (status, registry
   revision, no fallback), plus an unqualified-registry case asserting zero paid
   calls. `_policy` is typed with the real `ProviderGrant` contract.

## Files

Backend: `navox/knowledge/ask_contracts.py`, `navox/knowledge/ask.py`,
`navox/api/knowledge_ask.py`, `navox/api/knowledge.py` (router include),
`navox/db/knowledge.py`, `migrations/versions/0031_knowledge_intelligence.py`,
`navox/core/settings.py`, `navox/ai/prompts.py` (additive artifact),
`tests/test_knowledge_ask.py`, `tests/test_knowledge_migration.py`, plus the
one-token schema lists in `scripts/check-api.sh`, `.github/workflows/ci.yml`,
`tests/test_api_preflight.py`.
Web: `packages/contracts/src/knowledge.ts`, `apps/web/src/lib/knowledge-ask.ts`
(+ tests), `apps/web/src/components/knowledge-ask.tsx` (+ module CSS, tests),
`apps/web/src/components/search-workspace.tsx` (Ask-mode mount).

## Verification

| Command | Result |
| --- | --- |
| `pytest tests/test_knowledge_ask.py -q` | 28 passed, 1 skipped (SQLite) |
| same with the disposable PostgreSQL DSN | 29 passed |
| full knowledge cohort + preflight on SQLite | 270 passed, 2 skipped |
| full knowledge cohort on PostgreSQL (pre-correction run) | 243 passed; the corrected Ask file was re-run separately, 29 passed |
| `mypy navox` | clean (270 files) |
| `ruff check` on Ask files/tests | clean |
| `npx tsc --noEmit` / `npx vitest run` | clean / 31 files, 227 tests passed |
| `npx biome check .` / `npx next build` | 0 errors (9 pre-existing warnings) / build succeeded |

## Remaining limits

- No qualified live model exists; gateway evidence is a synthetic adapter behind
  the real runtime.
- Native News answers remain excluded with explicit coverage (searchable).
- Retention cleanup is implemented and exported but not yet registered in root's
  reconciliation schedule: call
  `await purge_expired_history(database, now=None, retention=timedelta(days=30))`.
- Browser/visual QA and the combined full gate stay with root's final phase.

## Decisions for Astra to confirm

- An exact replay returns the owned turn (`replay: true`, 200) from the service;
  409 remains for a reservation whose turn no longer exists (for example after a
  history clear or retention purge).
- Retained paid reservations store identifiers and trace metadata only, never a
  question or evidence text.
- `#N` follows displayed result order of the previous answer, per the review.

## Second high-assurance correction cycle

1. `bound_evidence_for_ids` now projects metadata with `load_only` under
   `populate_existing`, reads chunks fresh, and re-reads both *after* the text
   read; a resource is dropped unless title, classification, canonical URL,
   version, timestamps, deleted flag, scope and the exact chunk locator digests
   are identical before and after. `publication_check` still binds authority,
   exclusions and the canonical hash.
2. Snapshots persist the full non-text `binding()` (hash, identity, scope,
   classification, title digest, version, locator digests, fact values) and
   `_restore_citations` compares it exactly before rebuilding citations, so a
   renamed title or changed fact withholds the stored answer.
3. Replay is per owned request identifier across sessions
   (`_turn_for_user_request`) before any session creation, eligibility or
   provider work; `_bounded_session` locks the user row before counting/pruning,
   and free search turns repeat idempotently without creating a second session.
4. `finalize_turn` re-reads and locks the session and turn, requires the
   `RESERVED` state, and raises `TurnVanished` instead of writing a deleted row;
   the paid wrapper finishes the reservation and returns a clean refusal, and the
   clear-during-provider branch returns `results=()` so erased evidence is never
   echoed.
5. `purge_expired_history` deletes up to 500 expired turns first, then up to 200
   expired or now-empty sessions, and never commits — the caller owns the
   transaction. Paid reservations survive.
6. Early replay honours the restored `changed` flag and returns a `WITHHELD`
   answer instead of stale citations.
7. `_results_now` re-checks current membership and pause on every response path,
   including failed output and context misses, so a mid-call pause surfaces as
   `KnowledgeUnavailable` (403) rather than evidence.

New regressions added: null-version hash change, classification increase, altered
chunk text, revoked non-selected displayed result, fact-only replay, duplicate
index rejection, cross-session free replay, clear-during-provider empty results,
title-binding replay withholding, mid-call pause refusal, bounded retention with
no internal commit, and a PostgreSQL concurrent identical-request test.

Final verification on this tree: Ask suite 30 passed / 1 skipped on SQLite and
**32 passed** on PostgreSQL; subset cohort 95 passed / 2 skipped; `mypy navox`
clean (271 files); ruff clean; web `tsc`/`vitest`/`biome`/`next build` unchanged
from the previous cycle.

Retention hook for root reconciliation (caller commits):

```python
removed = await purge_expired_history(
    database, now=None, retention=timedelta(days=30), turn_limit=500, session_limit=200
)  # -> int, number of owned sessions deleted
```
