# SPEC-005 implementation and acceptance status

Source of truth: `SPEC-005_NavoX_Multi-Model_AI_Gateway(1).pdf`, supplied by the
owner. The SPEC-004 baseline is `ecdde5afde39ab352978f1e0fd9bf75dbe4d6c08`, merged
through PR #14. SPEC-005 remains a draft in PR #15; this is not a completion or
production-quality claim.

## Implemented in this draft

- Immutable provider-neutral contracts and all eleven logical profile names;
  revisioned database catalog, compiled schemas, versioned prompts, and explicit
  operator-controlled model assignments. No live models or prices are seeded.
- OpenAI, Gemini, Claude and Grok HTTP adapters with the same structured-text
  contract, bounded responses and fixed error codes. Adapters explicitly decline
  vision, embeddings and native tool execution.
- Live ownership, membership, connector, read-capability, source-revision and
  sensitivity checks around minimized context. Credentials are rejected; source
  instructions cannot alter routing policy or grant permissions.
- Eligibility before scoring, aggregate budgets/timeouts/fallback, persistent
  health and half-open probes, post-call authorization checks, content-free task
  traces, explicit shadow traffic and staged canary/rollback controls.
- Automatic-mode SPEC-002 extraction integration for Google and universal
  connectors. SPEC-004's deterministic subscription evidence processing remains
  on the shared ingestion path. Legacy OpenAI extraction remains an explicit
  compatibility mode; automatic routing is opt-in.
- Versioned Gmail reply drafts with edit/regenerate history, exact recipient,
  subject, body, version and hash approval, and the existing SPEC-001 send and
  independent-verification path. Editing invalidates approval. This slice
  supports one recipient and no attachments, CC or BCC; unsupported fields fail
  validation rather than disappearing. Drafts follow source deletion controls.
- Advanced provider preferences and scoped usage summaries. Preferences cannot
  add provider grants. NavoX-owned session/turn references work across providers;
  this is session infrastructure, not the SPEC-008 NavoXbot interface.
- An operator CLI for catalog/policy publication, measured synthetic extraction
  evaluations, explicit rollout changes and rollback. It never sends email.

See [the gateway operator guide](spec-005-gateway.md) for activation, boundaries,
evaluation limits and rollback instructions.

## Acceptance evidence and limits

The regressions below use authored HTTP responses, fake adapters or fake Gmail
gateways. They do not prove that a live model generates a correct answer.

| Demo | Regression evidence |
| --- | --- |
| A: four adapters, one extraction schema | `test_ai_providers.py` |
| B: interactive/background ranking | `test_ai_control.py` |
| C: eligible fallback after preferred-provider failure | `test_ai_runtime.py` |
| D: sensitivity and scope remain hard limits | `test_ai_runtime.py`, `test_ai_context.py` |
| E: generate, edit, approve, exact send, verify | `test_communication_drafts.py` |
| F: editing invalidates the old approval | `test_communication_drafts.py`, web draft tests |
| G: source instructions cannot expand policy/context | `test_ai_context.py`, `test_ai_sessions_tools.py` |
| H: total failure produces no fabricated result/action | `test_ai_runtime.py` |
| I: two providers share one NavoX session | `test_ai_sessions_tools.py` |

`test_ai_feature_ingestion.py` also exercises two resources through the actual
connector runtime, automatic feature adapter, validator, durable task traces and
ingestion receipts. CI includes it in the PostgreSQL pass to detect locks held
across the independent trace writer.

Required local preflight is `bash scripts/check-api.sh`, followed by the existing
web lint, typecheck, test and production build commands. CI must pass all six jobs
on the exact published head, including disposable PostgreSQL migration reversal,
real-session boundary tests and Compose/Temporal integration. The PR records the
results for its exact head; the original isolated foundation draft's tests are
superseded and are not release evidence.

## Still required before SPEC-005 sign-off

1. Review exact current model IDs, capabilities, prices and provider contract
   behavior, then measure all four real providers on the same approved corpus.
   No live provider calls or real email sends were performed during this draft.
2. Extend evaluation ingestion beyond the synthetic extraction smoke report.
   Production extraction precision/recall and task-specific planning, drafting
   grounding, edit preservation and assistant quality need reviewed corpora and
   rubrics. The smoke score is not those measurements. Communication and other
   profiles have no CLI qualification path yet; keep their traffic disabled
   rather than inserting invented scores.
3. Complete domain contracts/integration for features using the generic planning,
   meeting, assistant and ranking prompt registrations. Those are templates, not
   evidence that those product flows exist.
4. Demonstrate the versioned email workflow on an explicitly approved test
   mailbox through Temporal, and run an observed canary/rollback exercise.
5. Measure SPEC-005 trace coverage and security/quality targets over the approved
   acceptance runs. Passing behavioral tests does not establish a production
   percentage, security certification or full completion.

Keep PR #15 draft and unmerged until those acceptance gaps are resolved. The
default remains `AI_PROVIDER=disabled`; a configured legacy deployment keeps its
existing `openai` behavior until the operator explicitly switches it.
