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

- Physical-phone/network tests and true provider-cap duration. Live provider and emulated-browser evidence is recorded below; it does not qualify physical audio continuity.
- A crash after playout but before commit may lose the last turn; server playout is not proof of physical hearing. A call whose renewable lease has expired must end.
- Long-duration and larger-corpus qualification of the implemented lossy context compaction. Provider rollover does not promise verbatim recall indefinitely.
- Live cost reconciliation, licensed wake assets and immediate post-keyword speech qualification. The dedicated worker/cleanup test deployment is running.

Migration 0056 adds checkpoint storage; its populated downgrade guard refuses silent data loss. See the acceptance checklist and verification record for measured gates. Do not claim forensic erasure from historical backups.


## Long-call implementation update

Picovoice approval remains external and does not block this work, per Michael.
The configured worker now summarizes an older prefix in the background at an
operator-configured UTF-8 context threshold (48,000 bytes by default), retaining
recent full turns. The summary is a typed, explicitly lossy context note, not a
fabricated user/assistant exchange or an approval. It uses the existing metered
text route with no tools and no provider storage request. An incomplete/oversized
result leaves the original context intact and defers retry.

Compaction commits through the existing encrypted, owner/session/epoch-fenced
checkpoint. Concurrently completed turns are preserved; stale results cannot
overwrite a replaced prefix. Summaries share the active call's expiry and Forget/
hang-up cleanup, and are not cross-call memory. Recent full turns take precedence.
The persisted format reads existing dialogue-only checkpoints; old deployments
cannot read a summary checkpoint. End active test calls before deployment/rollback.

Provider replacement now also runs under context pressure (64,000 reported/
conservatively estimated tokens by default), not only session age. A warmed
replacement cannot displace newly started speech or reopen a closed connection.
After compaction it switches to the compacted history at the next quiet boundary.
No greeting or automatic response is requested during replacement.

64 PostgreSQL-backed/context/inference tests passed, including encrypted summary
restore and terminal purge. Nine focused compaction tests cover pending speech,
stale results, preserved corrections, invalid summaries and close-during-warmup.
Scoped lint and type checking passed. Live accelerated rollover, real-provider
compaction and worker-crash checks passed as detailed below. True provider-cap
and physical-phone acceptance remain open; this change does not promise
verbatim recall indefinitely.

See the [long-call verification record](vox-m1-verification.md) for measured live
provider/browser results, the failed offline test and the remaining acceptance gates.

Final live network verification retained one media admission across 20 seconds
offline and answered an immediate contextual follow-up correctly. Mute/unmute also
retained one admission and context. Both calls received audio and closed with 204;
all final output segments had zero measured source gaps. See the verification
record for numeric usage, earlier failed attempts and still-open device gates.
