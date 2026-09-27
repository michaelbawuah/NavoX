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
  the selected-state assistant uses those sessions across real API turns. The
  future SPEC-008 NavoXbot conversation interface remains separate.
- Typed v2 planning, meeting preparation, assistant and ranking contracts,
  authenticated selected-state APIs and a Today entry point. Models select saved
  facts, bounded preparation tools and advisory ordering; NavoX renders the saved
  values. No state mutation or tool execution occurs. All imported source edges
  require current authorization without rehydrating email bodies. Each domain
  has an independent fixed 12-case quality/safety corpus.
- An operator CLI for catalog/policy publication, measured synthetic extraction
  and reviewed communication evaluations, operational evaluations, explicit
  rollout changes and rollback. Read-only readiness exports propose catalog
  changes without publishing; trace coverage uses an explicit request manifest.
  Drafting uses 16 fixed cases and an exact-artifact review of grounding, user
  instructions, preserved edits and action claims. It never sends email.

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
| I: two providers share one NavoX session | `test_ai_sessions_tools.py`, `test_ai_operational_domains.py` |

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

## Owner-provided live checkpoint

The latest reviewed receipt is catalog r4 (digest
`3074bfb2edbc8c03a36ef6543ce646928fb9423a6ea5d4a6308b59b8c3e68dc1`).
Grok is absent at the owner's request. The three remaining models have PUBLIC-only
sensitivity ceilings; all nine assignments are at zero traffic with shadow off.

Gemini and Claude each have recorded r4 `communication_draft@v2` evidence at
15/16 (93.75%) with all seven safety cases passing. The retained failures are
Gemini's preserve-uncertainty edit and Claude's preserve-budget-edit greeting.
These satisfy the existing 90% drafting quality floor for that exact binding.
OpenAI's r3 16/16 drafting and prior extraction evidence remain historical.
This checkpoint does not establish personal-data serving or general reasoning.

Application drafting now requests v2 with the unchanged v1 output schema. The
four new operational prompt/schema registrations require a subsequent catalog
publication. Neither publishing new source code nor preparing a catalog proposal
modifies the live database. A new catalog revision invalidates old serving
evidence; it never relabels prior scores or deletes their history.

## Still required before SPEC-005 sign-off

1. Review the final catalog and approved data boundaries before rerunning paid
   qualification. The current PUBLIC-only model ceilings cannot serve real saved
   tasks or email. PERSONAL proposals are review artifacts, not permission grants.
2. Run the complete public corpora on the final catalog and record exact-bound
   passing evidence for each enabled task. Drafting requires human review of the
   actual 16 new candidates. Keep unqualified model/task combinations disabled.
   The new operational suites measure bounded fixture relevance, grounding,
   preparation-tool choice and ordering, not general production reasoning quality.
3. Observe A–I on the approved deployment, including real provider fallback,
   session continuity and the versioned test-email workflow through Temporal.
   An exact test send requires separate approval of the final recipient/content.
4. Run an explicitly approved, observed canary and rollback. Measure trace
   coverage and the remaining security/quality acceptance targets over recorded
   requests. Green local tests and CI alone do not prove live acceptance.
5. Retain receipts and owner acceptance with the exact source head, complete
   local gates and all six hosted CI jobs. Keep PR #15 draft until those records
   support completion.

The [final acceptance procedure](spec-005-live-acceptance.md) contains the ordered
steps, corpus/profile mapping, review-only export command and measurement format.
The default remains `AI_PROVIDER=disabled`; a legacy deployment keeps its existing
`openai` behavior until the operator explicitly switches it.
