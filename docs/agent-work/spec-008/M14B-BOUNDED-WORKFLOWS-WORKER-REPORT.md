# SPEC-008 M14B: bounded personal goals and durable verification

Baseline: 303d77889e4f2916f177b0393eb295bb8ede89d7 on
spec-008-navoxbot. Draft PR #22 remains draft. This is an implementation
checkpoint, not SPEC-008 live acceptance.

## Result

NavoXbot records a scoped goal after a saved Today briefing or meeting-prep
turn and after an exactly bound SPEC-001/003 communication action is prepared.
The TypeScript Temporal worker receives only an opaque goal UUID. It re-reads
the owning service's saved turn or action ledger; it does not call a provider,
approve, execute, retry or cancel an action. Only a completed action with an
independent verified_at can verify a communication goal. Approval pending,
in-flight, executed but unverified, uncertain and failure states retain
distinct truthful outcomes.

The PostgreSQL goal table is fenced by session, workspace and user IDs.
Creation checks the saved turn's exact session or the action's account within
the insert statement. Replays must match the same session. Worker reads
recheck source ownership before any status write, and a completed goal cannot
be downgraded by a replayed or late run. Partial unique indexes give one
goal per saved turn/kind or action. Detail values are fixed labels, not source
content, and the Temporal history test asserts an ID-only input payload.

Each run polls at most 12 times, 5 minutes apart, within a 60-minute window.
An active run is reused by deterministic workflow ID. A closed run can start
again after an explicit authenticated redispatch, and the atomic persisted
budget caps all starts, including explicit retries, at three. A missing
Temporal target or a start failure is recorded as DISPATCH_FAILED; an
unverifiable source never becomes COMPLETED. When the window ends while an
action waits for approval, external verification or execution, that status is
preserved and the UI offers a retry. A retry whose budget is exhausted reports
dispatched: false rather than claiming a new run.

The shared TypeScript contract, assistant runtime/store, Next API routes and
goal list, Temporal client/workflow/worker, 0002_assistant_goals.sql migration,
Compose service and configuration are in this patch. The worker owns its own
queue and leaves the SPEC-001/003 agent queue unchanged. The Web view refreshes
goals every 30 seconds while a session is open.

## Local verification

- Repository npm run lint, npm run typecheck, npm run test, and npm run build:
  passed. The runtime's ordinary suite has 343 passed and 14 environment-gated
  skips; its disposable PostgreSQL/Temporal run has 357 passed and zero skips.
  Web lint has nine pre-existing CSS specificity warnings and no errors.
- TypeScript migration checker: 44 assertions passed with the live disposable
  PostgreSQL schema. Alembic head and Python-owned tables remain unchanged.
- Real TypeScript Temporal worker: verified a saved briefing, checked the
  exact ID-only history payload, and restarted a closed successful workflow
  ID to verify a goal whose source became available later.
- The worker has a dedicated glibc-based Node image because Temporal's native
  core bridge cannot load in the Alpine Web image. The one-shot Compose
  assistant-migrate service applies the TypeScript schema before Web or the
  worker starts, avoiding concurrent migration races. Both images build; the
  migration container applied 0001 and 0002 against the disposable database,
  and the worker container reached RUNNING on the disposable Temporal server.
- Full API gate and exact-head hosted CI are root-owned pre-push and post-push
  gates respectively; record their final outcome in the PR/checkpoint rather
  than treating this report as proof they passed.

## Limits

The worker image installs tsx as a development dependency to launch the
TypeScript worker. A future pruned production image needs a compiled worker
entry. The first dispatch connects to Temporal on the request path with a
5-second timeout; outages leave a truthful failed-dispatch record. The
30-second UI poll is a view refresh, not a new verification attempt. Live
voice, connected-app, Gmail send and real-device SPEC-008 acceptance remain
separate from this checkpoint.
