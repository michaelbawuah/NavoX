# M16 — connected conversation closure

Baseline: `17c68bbd0e5e48654c7f74108e2b59d907175ab9` on
`spec-008-navoxbot`. Hosted CI run 36961726341 passed all six jobs. The
working tree has unrelated, untracked SPEC-006/007 evidence; leave it alone.

## Product boundary

Close the three SPEC-008 R3 implementation gaps found in scenarios G, I and M:
an authorized email/thread question must expose source-backed content, a current
email draft must be readable aloud on explicit request, and a natural reference
such as “take me to that” must yield a guarded navigation target from this
assistant session. Preserve the existing exact email approval and independent
verification flow. Canvas, X and Team NavoX are deferred. No auto-send,
auto-approval, background wake, or new AI provider is in this bundle.

## Contracts and safety

1. Reuse SPEC-007 grounded retrieval/Ask or its resource-detail contract for
email content. The assistant may show only current permission-checked,
source-backed excerpts/citations; provider prose, a title, or a thread listing
must not be presented as the message content. On disabled Ask, incomplete
coverage, changed authority, ambiguous matching, or thread-only evidence,
qualify the answer and keep draft/approval unavailable until one exact `EMAIL`
message is selected through a fresh current-source check. Preserve the
`EMAIL_THREAD` citation and offer a safe path to inspect it; do not infer an
individual message from a thread ID.

2. Add a planner-recognized read-only navigation intent for natural follow-ups.
The model may name a recent-turn ordinal, never a URL, resource ID, workspace,
connector or action. The TypeScript runtime resolves that reference solely from
the session's saved turn, requires exactly one eligible cited item or class
navigation target, and clarifies ambiguity. The response is a same-origin
guarded link, not an automatic external navigation. At click time, recheck
session ownership and the existing SPEC-007 resource-detail or class-navigation
authority; validate the exact source type and safe URL before redirecting.
No Canvas target for this release. A revoked, stale, mismatched or missing
target must fail closed. Version a new SPEC-005 planning prompt/schema as
needed; do not mutate the active v9 meaning in place. Astra will publish and
stage any new prompt only after review and evaluation.

3. Add an explicit “Read draft aloud” control for the saved current draft
version. Derive the spoken text server-side from the authenticated, source-turn
bound draft; the browser supplies selectors/version, never arbitrary speech
text or provider details. Reuse the existing SPEC-005 synthesis endpoint and
browser playback lifecycle. Reject a stale version or unsaved edits, cancel
playback on edit/stop/unmount, and do not speak automatically or imply that
audio review approves sending. Preserve visual full-text review. Respect the
existing 600-character synthesis bound; for longer drafts provide an honest
visual fallback or a bounded chunked implementation with explicit indication
of any text not spoken. Never silently truncate exact send content.

## Implementation and verification

Worker owns in-scope changes in `packages/contracts/src/assistant.ts`,
`packages/assistant-runtime/src/**`, `apps/web/src/lib/assistant-*.ts`,
`apps/web/src/components/navox-assistant*`,
`apps/web/src/components/assistant-email-actions.tsx`,
`apps/web/src/app/api/v1/assistant/**`, and the narrow SPEC-005 planning
contract/prompt files under `services/api/navox/ai/` plus focused tests. Existing
SPEC-007 services are reused; change their code only if a clearly identified
contract defect prevents this bundle, and report that before expanding scope.
Do not edit the M16 brief, acceptance matrix, checkpoint, unrelated SPEC files,
Docker/Compose, deployment settings, secrets, or provider catalog state.

Tests must cover a unique current email, a thread-only hit, ambiguous and
incomplete search, revocation/account switch, exact recent-turn navigation,
multiple/foreign/stale targets, no outbound action on navigation, current draft
speech, edit/version invalidation, long draft handling, playback cancellation,
and continued exact-approval semantics. Run focused TypeScript/API tests, lint
and typecheck for touched packages; report results and limitations. Do not run
paid provider calls, commit, push, deploy, or claim live acceptance. Astra will
review the patch, run the full pre-push gate, operate the catalog, and own live
acceptance.
