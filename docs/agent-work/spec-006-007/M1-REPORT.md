# SPEC-007 M1 integration and validation report (S007-M1-INTEGRATE)

STATUS: ready_for_review (not self-accepted; Astra owns acceptance)

Task: S007-M1-INTEGRATE in `/Users/mba_steins/NavoX`, branch
`spec-006-news-intelligence`, baseline HEAD
`5573ba4345a69cf4d01f793cfa4a6d0dd195a6f6`. Sibling source checkout
`/Users/mba_steins/NavoX-spec007` (branch `spec-007-universal-search`, HEAD
`9b36ee44a1b45837bd09b585c4d7afb2bd473c4b`) was read only. No commit, stage,
push, merge, deploy, publication, provider call, source enablement or
permission change was performed. No dependency, lockfile or credential file
was touched.

## Integration method

- The four shared paths were merged as a diff against sibling HEAD, not copied:
  `git diff` was taken in the sibling worktree for `.github/workflows/ci.yml`,
  `scripts/check-api.sh`, `services/api/navox/db/models.py` and
  `services/api/tests/test_api_preflight.py`, then applied with
  `git apply --check` + `git apply` in this checkout
  (`/private/tmp/m1-shared-files.patch`, 6804 bytes).
- `git diff 9b36ee44 5573ba43 -- <those four paths>` is empty, so both
  baselines were identical for the shared paths and the sibling diff applied
  cleanly with no News/CI conflict. No whole shared file was overwritten.
- The eight unique M1 paths were copied byte for byte and each verified with
  `shasum -a 256` against the sibling (8/8 identical). No sibling
  `__pycache__`, historical document, environment file or git state was copied.
- Newer News work (dirty web/API/doc paths and commit `5573ba4`) was left
  untouched; nothing was reverted.

## Changed paths and behavior

New (copied from the reviewed sibling draft):

- `services/api/navox/knowledge/__init__.py` (9 lines) - package docstring only.
- `services/api/navox/knowledge/contracts.py` (286) - `ResourceType`,
  `PrincipalType`, `Permission`, `KnowledgeResourceInput`,
  `KnowledgeChunkInput`, `PermissionGrant`, `STRUCTURED_RESOURCE_TYPES`,
  `aware_utc`/`stored_utc`, `validate_source_read_capability`,
  `principal_matches`, `permission_interval_active`.
- `services/api/navox/knowledge/permissions.py` (250) - `can_view_resource`,
  `effective_read_capabilities`, `_current_authority`, `_legacy_anchor_active`,
  `_canonical_source_is_live`, `_member_and_user_current`,
  `_has_effective_view_grant`. Read-only; no prompt, provider call or external
  action.
- `services/api/navox/db/knowledge.py` (196) - `KnowledgeResource`,
  `KnowledgeResourcePermission`, `KnowledgeChunk`.
- `services/api/migrations/versions/0029_knowledge_foundation.py` (254) -
  additive migration after `0028_news_retrieval`; symmetric downgrade.
- `services/api/tests/test_knowledge_contracts.py` (302),
  `services/api/tests/test_knowledge_foundation.py` (1579),
  `services/api/tests/test_knowledge_migration.py` (319).

Modified:

- `services/api/navox/db/models.py` (+19) - registers `navox.db.knowledge`; adds
  `uq_connector_connections_workspace_identity (workspace_id, id, user_id)` and
  `uq_connector_resources_workspace_connection_external_id (workspace_id,
  connector_connection_id, external_id, id)`. Both key sets already contain a
  unique column, so connector identity and behavior are unchanged.
- `scripts/check-api.sh` (+1) - three knowledge tables in the schema gate list.
- `.github/workflows/ci.yml` (+3/-1) - same three tables in the required-schema
  loop and the three knowledge modules in the disposable-PostgreSQL job.
- `services/api/tests/test_api_preflight.py` (+1) - the gate test's fake `uv`
  schema list mirrors `scripts/check-api.sh`; test-list-only, required for the
  scripted schema gate to stay internally consistent.

Bound contracts held: exact `(workspace_id, source_connection_id,
external_resource_id)` identity including soft deletes and across resource
types; composite provenance and owner FKs; explicit VIEW only; independent
COMMENT/EDIT/OWNER; nullable lower permission bound; WORKSPACE grants bound to
their own workspace; GROUP fail-closed; PUBLIC confined to authenticated
membership; live membership/user/source/definition/capability/ACL re-read on
every call; requesting-user and source-owner pause both honored; manifest
identity mismatch denied; `COMMITMENT`/`SUBSCRIPTION`/`NEWS_STORY` denied until
adapters exist; chunks inherit resource authority; no chunking, embedding,
vector, full-text, cache or session implementation.

## Verification (commands, exit status, result)

Environment: `UV_CACHE_DIR=/private/tmp/navox-uv-cache`, `UV_OFFLINE=1`, locked
`.venv` in `services/api`. Logs kept outside the source tree.

| Command (cwd) | Exit | Result |
| --- | --- | --- |
| `uv run --no-sync python -m pytest tests/test_knowledge_contracts.py tests/test_knowledge_foundation.py tests/test_knowledge_migration.py -q` (services/api) | 0 | 106 passed, 1 warning (alembic `path_separator` deprecation) |
| `uv run --no-sync python -m pytest tests/test_api_preflight.py -q` (services/api) | 0 | 17 passed |
| `uv run --no-sync python -m ruff format .` (services/api) | 0 | 385 files left unchanged |
| `uv run --no-sync python -m ruff check .` (services/api) | 0 | all checks passed |
| `uv run --no-sync python -m mypy navox` (services/api) | 0 | no issues in 241 source files |
| `uv run --no-sync python -m alembic upgrade head --sql` (services/api, `DATABASE_URL=postgresql+asyncpg://navox:navox@localhost:5432/navox`) | 0 | 2106-line offline PostgreSQL chain ending in the three knowledge tables |
| `bash scripts/check-api.sh` (repo root) | 0 | `API preflight PASSED`; 11/11 gates |

Full gate detail (`/private/tmp/m1-check-api.log`, run 2026-09-29
16:50:44-16:54:09 local): Ruff lint PASS, Ruff formatting PASS, strict mypy
PASS, Alembic revision width PASS, full API suite PASS (`2180 passed, 4
warnings in 197.71s`), SPEC-003 metric thresholds PASS, SPEC-004 fixture
thresholds PASS, Alembic SQL and required schema PASS, deterministic release
evaluation PASS (`invariants_passed: 11/11`, `control_coverage.passed: 11/11`,
`malicious_rejection_rate: 1.0`), patch whitespace PASS.

The 106 knowledge tests are the 82 pre-correction tests plus 24 correction
regressions. The combined suite is 2180 tests, i.e. 2074 non-knowledge tests
plus the 106 knowledge tests; the sibling's pre-correction gate reported 2081
(1999 non-knowledge plus 82). The 75-test non-knowledge difference is this
checkout's newer News commit and its existing dirty News test additions, not
part of this integration.

### Independent correction validation (not only the worker's own tests)

- Direct contract probe of the exact regression Astra reproduced: a stored USER
  grant returns `False` for a foreign workspace, PUBLIC returns `False` for a
  foreign workspace and for a foreign user, a stored WORKSPACE grant returns
  `False` for a foreign workspace, and constructing a WORKSPACE grant whose
  `principal_id` is not its own workspace raises `ValidationError`. `COMMENT`,
  `EDIT` and `OWNER` all return `False` from `permits_view`.
- Rendered PostgreSQL DDL inspected directly (not via the test's parser):
  `knowledge_resources` carries
  `FOREIGN KEY(workspace_id, source_connection_id, owner_user_id) REFERENCES
  connector_connections (workspace_id, id, user_id) ON DELETE CASCADE`,
  `FOREIGN KEY(workspace_id, owner_user_id) REFERENCES
  workspace_memberships (workspace_id, user_id) ON DELETE CASCADE`,
  `FOREIGN KEY(workspace_id, source_connection_id, external_resource_id,
  source_resource_id) REFERENCES connector_resources (...) ON DELETE CASCADE`,
  the identity unique constraint, and both enum checks;
  `knowledge_resource_permissions` carries the principal, WORKSPACE-principal
  and nullable-lower-bound interval checks; `knowledge_chunks` carries the
  scope FK, index/token/page checks and the resource-index unique constraint.
  The two supporting unique constraints render as `ALTER TABLE ... ADD
  CONSTRAINT` before `CREATE TABLE knowledge_resources`, so the composite FK
  targets exist when the tables are created.
- Source hygiene: no `os.environ`/`getenv`/`httpx`/`requests`/`aiohttp`/
  `openai`/`anthropic`/`subprocess` use in the new knowledge, model or migration
  files, and no knowledge API/router registration was added.
- `.github/workflows/ci.yml` still parses; `bash -n scripts/check-api.sh` passes.

## Risks and limitations

- PostgreSQL execution of `0029` remains unverified locally. Escalation was not
  requested because the brief allows a disposable PostgreSQL only if already
  accessible: the Docker socket at `/Users/mba_steins/.docker/run/docker.sock`
  is reachable on disk but the daemon refuses the API connection (`permission
  denied`), and no `psql`/`pg_ctl`/`postgres`/`initdb` is installed. Evidence is
  therefore portable: ORM metadata comparison, offline PostgreSQL DDL render of
  `0029` and of the whole chain, plus SQLite execution of the new FK/check/
  unique/cascade constraints. Hosted CI now applies the full chain and runs the
  three knowledge modules against real PostgreSQL, but that job has not run on
  this unpublished diff.
- `scripts/check-api.sh` and `.github/workflows/ci.yml` duplicate the schema and
  test lists by design; both were updated with the same names.
- `can_view_resource` is a foundation predicate only. Later retrieval,
  embedding, session, cache and graph consumers must revalidate at their own
  context-assembly boundary and must never treat flattened text or an earlier
  check as authority.
- The predicate denies when the requesting user's own `agent_paused` flag is
  set (mirrors `owned_connector(require_active=True)`) and requires
  `connector_connections.user_id == knowledge_resources.owner_user_id`. Both are
  conservative carry-overs from the reviewed draft, not new decisions here.
- A concurrent, docs-only write by root landed just before this gate:
  `docs/agent-work/spec-006-007/SEARCH-CONTRACT.md` at 16:50:18 local (and
  `PLAN.md` at 16:47:52). The gate started 16:50:44 and neither file is in the
  API suite's inputs, so the result stands for the source tree. Root's announced
  source patch (`services/api/navox/news/questions.py`,
  `services/api/navox/news/conversations.py`,
  `apps/web/src/components/news-chat.tsx` plus focused tests) had not landed at
  gate time - those files still carry their pre-session mtimes and are absent
  from `git status`. A combined final gate is still owed once that patch lands.

## Decisions requiring Astra

1. Retain or drop the two conservative carry-overs above (requesting-user pause,
   owner equals source-connection owner). Both are fail-closed; dropping the
   second would permit workspace-owned resources with a distinct owner.
2. `source_resource_id` stays nullable so provenance-only shells can be stored
   and audited while remaining ineligible; making the schema forbid them would
   require a non-null composite FK and removal of the shell regression.
3. `tests/test_api_preflight.py` was edited (test-list only) because the fake
   `uv` fixture must mirror `scripts/check-api.sh`.

## Next checkpoint

M1 integration and validation are complete for review. No later SPEC-007
milestone was started; the M1 predicate is a prerequisite for the connected
retrieval phases, which remain Astra's to sequence. Hosted CI on the combined
head, a real disposable-PostgreSQL execution of `0029`, and the combined final
gate after root's News chat patch are the outstanding external checks.
