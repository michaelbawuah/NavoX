# Astra Ask acceptance review — correction required

Actual source review found blocking privacy/contract errors despite passing authored
tests. Fix all in one coherent correction. Keep your assigned Ask files; root continues
graph/lifecycle/News. No paidcalls, no activation. Focused tests first; avoid fullgate.

1. **Strong revision + exact excerpt binding.** AskContext only compares nullable
source_version; it serializes cached candidate text before revalidation and never
checks current classification, hash, exact excerpts/facts/title. Unchanged/null
provider versions allow stale private text and classification downgrades. Carry
application-validated canonical content hash, source connection/resource identity,
sensitivity, scope/parent and exact excerpt locators (chunk index + span/digest) in
candidate/snapshot. Rebuild current evidence for every context build and compare exact
payload/bindings; enforce PERSONAL floor plus max current sensitivity. No original cached
text enters context after a change. Session restore/current publication must use these
same bindings. Generic evidence_for_ids(query='evidence') reorders/extracts differently
than original question, so stored excerpt array indices are not stable locators.
Store no raw text in snapshots. Reconstruct structured fact selections too (currently
_restore_citations ignores fact_ids). Test hash changes+reindex with source_version=None,
sensitivity increase, altered chunks/title, multiple matched excerpts in different
order, and fact-only session replay.

2. **Every response path must reauthorize ALL displayed evidence.** answer_question
returns original tuple(evidence) after provider call even WITHHELD, failed/invalid,
insufficient and unavailable branches. That leaks revoked titles/bodies. Fence/rebuild
all results at real current time on every branch, including non-selected sources,
current flags/member/pause/exclusions/parent/hash/classification. evidence_for_ids
must fence after text reads too (currently only before). Current session membership
must be checked on read/list APIs and service; map KnowledgeUnavailable cleanly instead
of 500s. Test revocation mid-call for every output branch and a nonselected result,
plus mid-read folder move/hash and clock expiry.

3. **No model prose in persistence/rendering.** uncertainty is unrestricted model text,
stored in turn.selection and shown even after WITHHELD. It can copy private text or
assert unsupported facts, violating selection-only output/ID-only snapshots. Replace
with bounded application-owned reason codes/messages (or discard model note entirely).
No source-derived uncertainty survives revocation, including insufficient branch with
empty selected refs. Reject duplicate excerpt indices too; currently they render twice.

4. **Durable attempt/turn concurrency and early idempotency.** _record_turn computes
max(sequence)+1 with no session lock; reserve_ask refreshes neither cached User nor
membership. Session is created before replay/reservation, so repeats create orphan
sessions. Missing-provider/free-search repeated requests hit uniqueness errors or new
sessions. Lock/re-read User then owned session; reserve a durable turn and sequence
before provider call, no locks held across call. Perform request-id replay before
session creation/retrieval/eligibility, including after qualification disappears.
Record free/unavailable turns idempotently as well, preserving payment quota and
reservation after clear. Concurrent clear/delete during provider call must not restore
history or return stale evidence; finalize with current session/turn ownership and
atomic state transition. Enforce max turns BEFORE provider call. Tests: two concurrent
same-session distinct requests, simultaneous identical requests, free repeats,
clear midcall, prior request replay after provider eligibility changes, cached pause.

5. **Follow-up contract is wrong.** #N must mean Nth DISPLAYED RESOURCE in immediately
previous completed turn, not Nth turn in history. Preserve displayed order for all
results separately from selected citations. Parse explicit #N in question or referent;
reject multiple/ambiguous/missing/stale/out-of-range and conflicting followup_of.
followup_of may identify only that latest completed turn; it cannot silently bind to
another turn. Empty/stale focus must reject before provider, never fall back to a broad
new search. Update UI with visibly numbered source results and controls bound to that
order, not turn.sequence. Tests with two turns whose rankings differ and deleted #2.

6. **Bounded retention + privacy erase.** No retention is implemented. Add30-day question/
turn/session retention and bounded cleanup function callable while flags off; send
root the function signature to register in reconciliation. Keep paid reservation IDs
(no question/body) sufficiently durable for replay/no refund. Bound session creation,
not just list length. Free search-turn queries must reject credentials like Ask.
Allow authenticated owned history deletion when feature flags are off.

7. **UI races and paid retry IDs.** A failed submit generates a fresh request_id on
retry (can purchase twice after timeout). Preserve identifier/payload for retry of same
submission until resolved or input changes. Clear/delete must invalidate submission+
session gates and clear local answer/history immediately; late responses must not
restore erased content. Recheck gates after each awaited load; startFollowup has no
race/error fencing. Clear old answer on new request/failure. Cover these with browser/
meaningful component tests, not helper-only assertions.

8. **Real gateway verification.** test_knowledge_ask.py only FakeAskGateway; add an
actual RegisteredAskGateway + synthetic HTTP adapter + qualified registry fixture
(like semantic tests) exercising artifact/strictwire/trace + pre/post context, no
fallback. Confirm max-cost, prompt injection, classification and revision changes
at runtime boundary, and no paid HTTP call when unqualified. Remove new typeignore
_policy(grants:tuple[object,...]); type it with the actual ProviderGrant contract.

Root graph service/search signatures remain unchanged. New optional
SearchResponse.relationships and EvidenceBundleV1.relationships now carry bounded
current source-parent links. Root graph integration76tests pass. Root will register
your cleanup function and run final API/web/browser gates once correction reviewed.

## Astra integration after second correction (02:18UTC)
Second correction still had ORM identity-map aliasing in before/after payload checks,
cross-workspace request lookup, non-atomic session creation/free replay, and old UI
turn-number followups. Root took ownership of Ask integration and fixed these actual
source issues directly. Captured immutable metadata/chunk digests, indexed fact fields,
current scope, URL/update timestamps and parent binding now fence evidence; persisted
fact values are hashed. Relevant later chunks are selected by question terms. User
lock covers replay/session/turn reservation before retrieval; finalization follows
User->Session->Turn order. Retention locks parents first, clears expired session titles,
removes bounded old turns even in active sessions, and runs in reconciliation while off.

Root added exact regressions for identity-map mutation, cross-workspace same request,
concurrent free requests, and later-chunk selection. SQLite47passed/3PGskips across
Ask+lifecycle, PostgreSQL38Askpassed. New unavailable-provider label correction and
fact-only/no-body support receive final combined cohort verification next.

Real rebuilt browser authored fixture checks passed stable retry IDs, resource#2,
clear-during-call, explicit refresh and source conflict cards. Root visual inspection
found a mobile flex-basis gap; CSSfix and final rebuilt browser rerun pending.
No live model/qualification/owner data used; these do not establish the original
query-quality/citation/injection corpus thresholds.
