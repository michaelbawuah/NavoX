# Milestone 1 continuity checkpoint

Implemented on the M1 draft branch on 2026-10-23. Real phone/network acceptance is still **not passed**.

## Implemented

- Bounded encrypted completed-turn checkpoint per call, with domain, owner, workspace, web session, call generation, revision and inference-configuration fingerprint binding. A replacement refuses a changed voice/model configuration. No raw audio, tool dumps, embeddings or saved-memory/history endpoint.
- The same physical PostgreSQL connection holds the exclusive worker-epoch lock and performs short User-serialized checkpoint operations. Fresh joined authority is checked before access and again before commit; a broken physical connection cannot reconnect silently. Concurrent revision updates have one winner.
- Configured workers restore checkpoints and clear only RAM on generic shutdown. Hang-up, session logout and reset purge storage transactionally. Renewable call expiry extends only still-live checkpoints. Independent cleanup erases expired ciphertext while retaining a content-free tombstone to prevent silent amnesia.
- Email, feed, material, saved-memory, planner-task, timetable-record and saved-Board erasure invalidate the entire active call and its media epoch because current turns lack source provenance. Content-free cost accounting survives call cleanup.
- Browser terminal disconnect during SDK recovery now requests a replacement with the same call ID after old resources close. Owner verification, mute/speaker/standby controls and hang-up fences survive. Only explicit HTTP 425 pre-allocation cleanup responses are retried (same request ID; 20 attempts, one second apart); no timeout, unknown side effect or authentication failure is replayed.
- Real admission refuses replacement when no live checkpoint exists, including legacy RAM-only calls. Old JWTs and expired call authority are never revived.

## Remaining

- Real LiveKit/OpenAI worker-process crash, terminal room replacement, network loss and device tests. The test suite proves database/browser transitions using synthetic providers, not physical audio continuity.
- A crash after playout but before commit may lose the last turn; server playout is not proof of physical hearing. A call whose renewable lease has expired must end.
- Explicit long-context compaction remains unimplemented; the current hard ceiling fails safely. Provider rollover alone does not solve context capacity.
- Dedicated worker/cleanup test deployment, live cost reconciliation, licensed wake assets and immediate post-keyword speech qualification.

Migration 0056 adds checkpoint storage; its populated downgrade guard refuses silent data loss. See the acceptance checklist and verification record for measured gates. Do not claim forensic erasure from historical backups.
