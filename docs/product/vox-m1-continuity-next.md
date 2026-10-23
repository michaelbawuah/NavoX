# Milestone 1 continuity work remaining

The current implementation recovers a bounded transient interruption in the same LiveKit room while its worker survives. It does not recover through terminal room/worker replacement or an expired live authority lease. Acceptance is **not passed**.

## Next implementation boundary

- Add one bounded encrypted active-call checkpoint per call, using the existing Fernet vault and a domain-bound envelope. Bind owner, workspace, authentication session, call generation, revision and expiry. Store only completed heard turns, no raw audio/tool result dumps or embeddings. This is temporary call execution state, not milestone 4 saved memory/history.
- Use the same physical database connection that holds the exclusive worker-epoch lock. Serialize access with the existing authority connection lock and repeat the fresh joined authority proof under the User row lock before decrypting/CAS. A new pooled session is not an adequate stale-worker fence.
- Replace the production in-memory context factory and make generic shutdown clear RAM without deleting an otherwise live checkpoint. Commit only after server playout and fresh authority succeed. Corrupt, expired or undecryptable context must produce an honest failure, not an invented continuation.
- Tie checkpoint expiry to the call and authentication-session lease. Only extend a still-live checkpoint. Purge transactionally on Hang up, call expiry, logout/session replacement, reset and privacy deletion; add bounded crash/orphan expiry cleanup to the reconciler.
- Source Forget cannot prove selective redaction from current Q/A pairs, which have no provenance. Conservatively invalidate the owner's active context and media epoch when forgetting email/feed/coursework sources. Show that context was cleared rather than silently retaining stale provider RAM.
- Add frontend terminal recovery only while the underlying reservation and account remain freshly authorized. Wait for old media cleanup, create a new epoch/request and let the replacement worker restore context. Never revive expired authority or reuse an old JWT.

A database checkpoint alone does not implement reconnect. A crash between physical playback and checkpoint commit can still lose the last turn; server playout is not proof of what a student physically heard. Do not save unheard generated answers as established conversation.

## Required gates

Actual PostgreSQL CAS/revocation races, physical lock-connection loss, encryption/domain binding, expiry/renewal, terminal frontend recovery, source Forget and stale-runtime fencing; then real network/device testing. Long-context compaction also remains a separate unresolved engineering item. No source-retention or off-record wording should promise forensic deletion from historical backups.

No migration number is allocated by this plan. The next available number must be checked against the voice-credit migration before implementation.
