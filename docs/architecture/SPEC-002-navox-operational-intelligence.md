# SPEC-002-NavoX-Operational-Intelligence-Engine

## Background

SPEC-001 establishes NavoX as an AI Operations Platform: integrations,
persistent operational state, the Commitment Graph, durable workflows,
permissions, approvals, safe execution, and the Today experience.

SPEC-002 defines the provider-independent intelligence layer that
converts authorized information into operational understanding. Gmail
and Google Calendar are the first inputs; future Canvas/LMS, Drive,
subscription, GitHub, browser, and third-party connectors must feed the
same canonical contracts.

Core pipeline:

``` text
Authorized Connector
→ Canonical SourceDocument
→ Operational Extraction
→ Evidence + Observations
→ Entity + Temporal Resolution
→ Commitment Resolution
→ Commitment Graph
→ State Inference
→ Attention Intelligence
→ Surfacing Policy
→ Today / Agent / Proactive Experience
→ User Feedback
```

Core invariant: **AI proposes facts; deterministic application code
decides what those facts are allowed to change.**

## Requirements

### Must

-   Event-driven, incremental Gmail and Calendar understanding.
-   Structured extraction of requests, promises, deadlines, meetings,
    follow-ups, tasks, people, dates, and relationships.
-   Versioned schema-constrained model outputs; models never directly
    mutate operational state.
-   Evidence/provenance for every inferred fact.
-   Entity resolution and commitment deduplication.
-   Temporal resolution with timezone/context and explicit uncertainty.
-   State inference, completion detection, waiting-on-external
    semantics, contradiction and supersession.
-   Deterministic Attention Intelligence and interruption policy.
-   Today derived from structured operational state.
-   Explainability: why it matters, why now, and source evidence.
-   Conservative confidence policy.
-   Feedback collection with bounded preference learning; preference
    never grants authority.
-   Idempotent reprocessing and historical correction.
-   SPEC-001 workspace isolation, minimal storage, permissions, approval
    and audit rules.
-   Automated extraction, resolution, ranking, security, and
    prompt-injection evaluations.

### Should

Correlate email threads and meetings; detect unanswered requests;
understand supported relative dates; suppress repeated low-value
notifications; distinguish informational/marketing content from
obligations; and create meeting-relevance signals.

### Could

Drive intelligence, recurring behavioral patterns, project-risk
inference, billing intelligence, richer relationship modeling, learned
contextual importance, organization terminology, and shared commitments.

### Won't --- SPEC-002

Unrestricted model/provider access; direct AI database mutation;
autonomous consequential actions; financial transactions/cancellation;
unrestricted self-learning; custom foundation-model training; graph
database; browser extension; connector marketplace; or implementation of
SPEC-003--007.

## Method

### Canonical Contract

Every connector normalizes data into a versioned `SourceDocument`:

``` python
class SourceDocument:
    id: UUID
    workspace_id: UUID
    provider: str
    source_type: str
    external_id: str
    external_parent_id: str | None
    author: SourceIdentity | None
    recipients: list[SourceIdentity]
    subject: str | None
    content: str | None
    occurred_at: datetime
    retrieved_at: datetime
    metadata: dict
```

Future connectors target this contract and do not receive custom access
to intelligence-engine internals.

### Processing

``` plantuml
@startuml
start
:Receive authenticated provider event;
:Deduplicate + retrieve minimum changed resources;
:Normalize to SourceDocument;
if (Operationally relevant?) then (yes)
  :Structured extraction;
  :Schema validation;
  if (valid?) then (yes)
    :Persist evidence + observations;
    :Resolve entities + temporal references;
    :Resolve commitments;
    :Apply confidence policy;
    :Update operational state;
    :Recalculate attention;
    :Apply surfacing policy;
  else (no)
    :Record extraction failure;
  endif
else (no)
  :Mark processed;
endif
stop
@enduml
```

### Observation and Evidence Persistence

``` sql
CREATE TABLE operational_observations (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspaces(id),
 user_id UUID NOT NULL REFERENCES users(id),
 observation_type TEXT NOT NULL,
 subject_person_id UUID REFERENCES people(id),
 object_person_id UUID REFERENCES people(id),
 action_text TEXT,
 object_text TEXT,
 effective_at TIMESTAMPTZ,
 confidence NUMERIC(4,3) NOT NULL,
 status TEXT NOT NULL DEFAULT 'ACTIVE',
 extractor_version TEXT NOT NULL,
 model_provider TEXT,
 model_name TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE observation_evidence (
 id UUID PRIMARY KEY,
 observation_id UUID NOT NULL REFERENCES operational_observations(id) ON DELERE CASCADE,
 connection_id UUID NOT NULL REFERENCES connections(id),
 provider TEXT NOT NULL,
 source_type TEXT NOT NULL,
 external_resource_id TEXT NOT NULL,
 evidence_locator JSONB,
 source_hash TEXT,
 observed_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE person_identities (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspaces(id),
 person_id UUID NOT NULL REFERENCES people(id),
 provider TEXT,
 identity_type TEXT NOT NULL,
 identity_value TEXT NOT NULL,
 confidence NUMERIC(4,3) NOT NULL DEFAULT 1.0,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(workspace_id, identity_type, identity_value)
);

CREATE TABLE intelligence_feedback (
 id UUID PRIMARY KEY,
 workspace_id UUID NOT NULL REFERENCES workspaces(id),
 user_id UUID NOT NULL REFERENCES users(id),
 target_type TEXT NOT NULL,
 target_id UUID NOT NULL,
 feedback_type TEXT NOT NULL,
 metadata JSONB NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Prefer bounded evidence locators over unnecessarily storing complete
source bodies.

### Resolution

Entity resolution order: exact provider/email identity → existing
mapping → calendar attendee → contextual similarity → AI-assisted
ambiguity.

Temporal resolution preserves the original expression plus resolved
instant/window, method, and confidence. Ambiguous dates become windows
rather than invented exact deadlines.

Commitment resolution proceeds through deterministic candidate
discovery, database matching, semantic similarity, then AI only for
ambiguity. Outcomes are:

`CREATE_NEW`, `MERGE_EVIDENCE`, `UPDATE_EXISTING`, `SUPERSEDE`,
`MARK_COMPLETED`, `MARK_WAITING`, `NO_OPERATION`, `NEEDS_CONFIRMATION`.

Supported MVP completion conditions: `ACTION_EXECUTED`,
`EXTERNAL_RESPONSE`, `EVENT_OCCURRED`, `DEADLINE_PASSED`,
`USER_CONFIRMED`, `SEMANTIC_OUTCOME`.

### Confidence

Initial calibration hypothesis: ≥0.90 high confidence; 0.70--0.89
candidate/confirmation; \<0.70 suppressed from user-visible commitment
creation by default. Final confidence combines model confidence, source
reliability, temporal/entity certainty, corroboration, and contradiction
penalties.

### Attention

Store normalized feature breakdowns for urgency, consequence, objective
relevance, user priority, actionability, waiting duration, relationship
importance, confidence, interruption cost, and notification fatigue.

Initial scoring hypothesis:

``` text
score =
  0.23*urgency + 0.16*consequence + 0.14*objective_relevance
+ 0.12*user_priority + 0.12*actionability + 0.08*waiting_duration
+ 0.05*relationship_importance + 0.10*confidence
- 0.08*interruption_cost - 0.08*notification_fatigue
```

Initial bands: 90--100 NOW; 70--89 TODAY_HIGH; 50--69 TODAY; 30--49
DASHBOARD; 0--29 SUPPRESS. Confidence ceilings, duplicate suppression,
fatigue, waiting windows, and imminent meeting preparation may override
raw scores.

### Today

`/api/v1/today` queries structured operational state for
`NEEDS_ATTENTION`, `COMING_UP`, `WAITING_ON`, and `RENEWALS`. An
optional LLM may improve wording but cannot invent underlying items.
Each item carries commitment ID, category, score, reason/factors,
evidence, and suggested capability.

### Durable Workflows

Add `ProcessSourceEventWorkflow`, `ReevaluateCommitmentWorkflow`,
`AttentionEvaluationWorkflow`, `TodayRefreshWorkflow`,
`FeedbackLearningWorkflow`, and `IntelligenceReconciliationWorkflow`.
PostgreSQL remains user-facing truth; Temporal owns durable execution
state.

### Security

External content is untrusted:

`SYSTEM SECURITY POLICY > USER-GRANTED PERMISSIONS > EXPLICIT USER APPROVAL > AGENT PLAN > EXTERNAL CONTENT`

External content may influence understanding but cannot grant authority,
alter permissions, authorize actions, or expose secrets.

## Implementation

1.  Add versioned provider-neutral contracts.
2.  Add observation/evidence/identity/feedback migrations and commitment
    extensions.
3.  Establish extraction and adversarial eval fixtures.
4.  Implement structured extraction through SPEC-001's AI-provider
    abstraction.
5.  Implement deterministic-first entity and temporal resolution.
6.  Implement commitment matching, deduplication, supersession, and
    state transitions.
7.  Connect Gmail and Calendar through `SourceDocument`.
8.  Implement completion/waiting-on inference.
9.  Implement deterministic Attention Engine with explanations.
10. Upgrade Today to structured operational state.
11. Add bounded feedback/preference learning.
12. Add reconciliation, telemetry, security tests, and regression gates.

Recommended Git sequence:

``` text
docs: establish SPEC-002 operational intelligence architecture
feat: add provider-neutral intelligence contracts
feat: add operational observation and evidence persistence
test: establish operational extraction evaluation corpus
feat: implement structured operational extraction
feat: implement person identity resolution
feat: implement temporal expression resolution
feat: implement commitment resolution pipeline
feat: connect Gmail events to intelligence pipeline
feat: connect Calendar events to intelligence pipeline
feat: implement commitment completion inference
feat: implement deterministic attention engine
feat: power Today from operational intelligence
feat: add intelligence feedback and preference learning
test: establish SPEC-002 intelligence regression suite
docs: document SPEC-002 implementation and evaluation results
```

**Work directive:** implement incrementally on top of SPEC-001. Do not
redesign SPEC-001 or prematurely implement SPEC-003--007. Verify current
official provider documentation and dependency versions immediately
before implementation/pinning. No secrets in Git; commit `.env.example`
only.

## Milestones

-   **M1 Contracts + Persistence:** canonical contracts, migrations,
    isolation, evidence.
-   **M2 Extraction + Evaluation:** schema-validated extraction and
    adversarial corpus.
-   **M3 Resolution:** entity, temporal, dedupe, contradiction,
    supersession.
-   **M4 Gmail + Calendar Vertical Slice:** real changes become
    operational state.
-   **M5 State Intelligence:** completion and waiting-on semantics.
-   **M6 Attention + Today:** ranking, surfacing, explanations, Today.
-   **M7 Feedback + Hardening:** preferences, reconciliation, telemetry,
    regression/security gates.

Required demos: 1. Email asks for a budget before tomorrow's meeting →
one evidence-backed commitment → deadline/meeting resolution → Today →
outgoing evidence resolves it. 2. Obtain approval → request sent →
`WAITING_ON_EXTERNAL` → approval response → `COMPLETED`. 3.
Newsletter/duplicate/prompt injection → no bogus high-priority
commitment, duplicate alert, escalation, or unauthorized action.

## Gathering Results

  Capability                                       Initial target
  ---------------------------------------------- ----------------
  Explicit commitment precision                              ≥95%
  Explicit commitment recall                                 ≥90%
  False commitment creation                                   ≤3%
  Duplicate commitment rate                                   ≤2%
  Exact person resolution with stable identity               ≥99%
  Explicit date resolution                                   ≥98%
  Supported relative-date resolution                         ≥90%
  Completion detection                                       ≥92%
  Waiting-on classification                                  ≥90%
  Today high-priority precision                              ≥90%
  Duplicate notification rate                                \<1%
  Cross-workspace leakage                                       0
  Unauthorized action from source content                       0
  Prompt-injection permission escalation                        0

These are validation targets, not assumed results. Production failures
should become anonymized/synthetic regression fixtures. Monitor useful
surfaced items, corrections, completion, Today usefulness/dismissal,
notification frequency, duplicate suppression, extraction failures,
provider latency/cost, workflow failures, and security-policy blocks.

SPEC-002 is complete only when the full chain works reliably:

`AUTHORIZED SOURCE → SourceDocument → Extraction → Evidence → Observation → Resolution → Operational State → Attention → Surfacing → Today → Feedback → Bounded Preference Adjustment`

For proactive intelligence, precision takes precedence over aggressive
recall.

### Future Compatibility (not SPEC-002 scope)

-   **SPEC-003:** Universal Connector Platform --- Canvas/LMS,
    apps/APIs, browser/local integrations, open connector contract;
    third-party marketplace/execution deferred.
-   **SPEC-004:** Subscription & Recurring Obligation Intelligence.
-   **SPEC-005:** Multi-Model AI Gateway --- OpenAI, Gemini, Claude,
    Grok with evaluated routing/fallback/privacy/cost/latency policies.
-   **SPEC-006:** NavoX News Intelligence --- permitted sources,
    clustering, summaries, provenance, original-source links.
-   **SPD