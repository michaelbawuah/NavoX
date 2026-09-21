# SPEC-001 NavoX AI Operations Platform

## Status

Authoritative architecture specification. This document is the contractor-ready source of truth for NavoX implementation. Changes to an architectural decision in this specification require an Architecture Decision Record (ADR) that explains the implementation evidence, alternatives, and consequences.

## Purpose

Knowledge work is fragmented across email, calendars, documents, spreadsheets, messaging, task systems, websites, financial and administrative systems, and AI tools. Humans remain the integration layer. Most AI assistants accelerate individual steps but remain reactive and stop at producing information.

NavoX is an AI Operations Platform for individuals, professionals, and eventually small teams. It maintains an authorized operational model of a user’s world and follows this loop:

> Observe -> Understand -> Remember -> Prioritize -> Offer Help -> Plan -> Approve -> Act -> Verify

**Positioning:** NavoX knows what needs your attention and helps you handle it.

**Signature question:** “NavoX, what do I need to know today?”

NavoX is not another AI application. It is the layer that lets AI operate the applications people already use. The user delegates outcomes, not clicks. NavoX should not merely remind a user what they need to do; it should help them do it.

Administrative, event, purchasing, budget, and programming workflows are useful test cases, but NavoX remains a general product rather than organization-specific software.

## Product requirements

### Must have

- Goal-driven operation with persistent objectives across sessions, days, and weeks.
- Authorized connected context, initially Gmail, Google Calendar, and Google Drive.
- A Commitment Graph with `DEADLINE`, `MEETING`, `FOLLOW_UP`, `PROMISE`, `RENEWAL`, and `TASK` commitments.
- Proactive intelligence and contextual reminders that explain what, when, why, and what NavoX can do.
- A Today briefing and conversational operational queries.
- Action proposals and preparation, with bounded multi-step plans persisted as data.
- Human-approved execution for consequential external actions, including exact-action approval and verification.
- Traceability and provenance for extracted commitments and surfaced information.
- User control over connected systems, permissions, notifications, autonomy boundaries, and the ability to pause NavoX.
- Privacy-first minimal storage; raw content remains in source systems.
- Zero-trust treatment of external content and deterministic policy enforcement.
- Workspace/tenant boundaries from day one.
- Durable workflows for waits, approvals, follow-ups, timers, and recovery.
- Meaningful Git history from project initialization; never commit secrets.

### Should have

- Subscription and renewal intelligence, meeting preparation, unanswered-message detection, deadline extraction, promised follow-ups, daily briefing, and intelligent prioritization.
- Structured operational memory with provenance, confidence, and validity.
- AI-provider abstraction and an evaluation framework.
- An audit/activity feed for consequential actions and authorization.

### Could have

- Bills and recurring payments, warranties, document expirations, reservations, application deadlines, package tracking, recurring organization processes, project-risk detection, and learned workflows.
- A Chrome side-panel extension after the web core loop.
- Microsoft 365, Slack, Notion, and GitHub integrations; team objectives/workspaces; mobile clients later.

### Not in the MVP

- Fully autonomous financial transactions or purchases.
- Automatic subscription cancellation without confirmation.
- Unrestricted email sending, destructive actions, or arbitrary browser/computer control.
- Native mobile apps, broad enterprise administration, or many integrations before the core loop works.

## Core architecture

The LLM is not the application. Models reason and propose. Deterministic NavoX code controls permissions, scheduling, state transitions, approvals, execution, retries, auditing, idempotency, and verification.

```text
Web client: Next.js / React / TypeScript
                |
          FastAPI API Gateway
                |
  +-------------+------------------+
  |             |                  |
Agent Runtime  Context /       Commitment /      Proactive
               Memory Engine   Graph Engine      Engine
  |             |                  |                 |
  +---------- AI Gateway ---------+              Temporal
                |
Policy Engine -> Approval Engine -> Tool Gateway
                                        | | |
                                 Gmail Calendar Drive
                |
PostgreSQL: operational state, memory, plans, approvals, audit
```

### Technology direction

- **Frontend:** Next.js, React, TypeScript.
- **Backend:** Python, FastAPI, Pydantic.
- **Persistence:** PostgreSQL.
- **Durable execution:** Temporal Python SDK.
- **Initial integrations:** Google OAuth, Gmail, Calendar, and Drive.
- **Development:** local containers and GitHub Actions CI.
- **AI Gateway:** begin with one primary provider behind clean interfaces and evaluation capability; adapters eventually support OpenAI, Gemini, and Claude.
- **Redis:** introduce only if implementation evidence shows a need for caching or ephemeral coordination. PostgreSQL and Temporal are the foundations.

Before dependencies or third-party APIs are pinned, implementation must verify current official documentation and supported versions. For the initial engineering foundation, the verified baseline is Next.js 16.3.5 with Node.js 20.9 or later, FastAPI with Pydantic v2, SQLAlchemy 2.x, and Temporal’s Python SDK. Exact package versions are locked in reproducible dependency files and reviewed by CI.

## Commitment Graph and lifecycle

A commitment connects a user/workspace, objective, source, person, action, event, and other commitments. PostgreSQL is used for the MVP rather than a graph database.

```text
Candidate -> Confirmed or Rejected -> Upcoming -> Attention Needed
         -> Action Prepared -> Awaiting Approval -> Executing -> Completed or Failed
```

`WAITING_ON_EXTERNAL` represents work dependent on another person or system. Sending an email does not necessarily complete an objective when the actual desired outcome is a reply or confirmation.

Extraction pipeline:

```text
LLM extraction -> structured JSON -> schema validation -> confidence policy
-> deduplication -> Commitment DB -> deterministic scheduler
```

High-confidence candidates may be created quietly. Moderate-confidence candidates should be confirmed. Low-confidence candidates should normally be suppressed. Evaluation determines the exact policy thresholds.

## Proactive intelligence

Time events include meetings, deadlines, renewals, follow-ups, and daily briefings. External events include new mail, calendar changes, relevant Drive changes, responses, and completed actions.

```text
attention score = urgency + consequence + user priority + objective relevance
                  + actionability + waiting duration
                  - interruption cost - notification fatigue
```

Very high scores may notify now; medium-high scores enter the briefing; medium scores remain on the dashboard; low scores suppress. Thresholds are evaluation parameters rather than universal truths.

Every surfaced item answers:

1. What is happening?
2. Why does it matter?
3. What can NavoX do about it?

## Today: signature experience

Today is dynamic operational state, not a static morning summary. If something resolves at 9:15, a later briefing must reflect that change.

Supported queries include:

- What do I need to know today?
- What am I forgetting?
- What is coming up this week?
- What am I waiting on?
- What needs my attention?
- What can you handle for me?
- Prepare me for my next meeting.
- Anything costing me money soon?
- What did I promise people?
- What is blocking this objective?

## Agent runtime and action engine

The **Handle This** flow is:

```text
Load commitment/objective context -> generate bounded plan -> persist plan
-> policy-check each step -> execute authorized read/prepare steps
-> wait for approval for consequential external action -> execute -> verify
-> update Commitment Graph
```

Initial limits are eight plan steps, two replans, and roughly five minutes of active execution. Durable waiting can last hours or days. If ambiguity materially affects an action, NavoX stops and asks rather than guessing.

Every action contract includes: `name`, `provider`, `input_schema`, `output_schema`, `risk_level`, `required_permissions`, `idempotency_policy`, `timeout`, and `verification_method`.

Initial tool set:

- Gmail: `search`, `read`, `create_draft`, `send`
- Calendar: `search`, `availability`, `create_event`, `update_event`
- Drive: `search`, `read`, `create`, `update`

## Risk model

| Level | Meaning | Default behavior |
| --- | --- | --- |
| R0 | Read | Automatic when authorized |
| R1 | Prepare | Automatic when authorized |
| R2 | Reversible mutation | Normally needs approval |
| R3 | External action | Approval required |
| R4 | Sensitive | Explicit confirmation and restriction |
| R5 | Prohibited | Never execute |

The model never chooses or overrides a risk level. Financial and destructive actions stay prohibited or heavily restricted in the MVP.

## Memory and context

Use structured memory rather than a giant permanent conversation transcript: Profile, People, Objectives, Commitments, Episodic, and Preferences. Learning a preference never grants authority.

The Context Builder assembles the smallest relevant context for each task and retrieves source material only when necessary. Every memory fact carries provenance, confidence, `valid_from`, `valid_until`, and `last_verified_at`, so stale facts do not become permanent truth.

## Privacy and provider boundaries

Do not duplicate entire Gmail or Drive content. Store normalized facts, external IDs/references, extracted commitments, useful hashes/metadata, plans, actions, approvals, and operational state. Retrieve original content from Google when needed.

The AI Gateway is the only model-provider boundary. It performs minimization/redaction, structured-output validation, timeouts, telemetry, and provider-specific handling.

## Event-driven integration

Prefer provider events over repeated LLM rescans:

```text
Google -> authenticated ingestion -> deduplication -> normalization -> incoming_events
       -> structured state updates -> Proactive Engine
```

Provider-specific payloads become canonical NavoX events. Google watch/subscription renewal is durable infrastructure; NavoX must not assume provider subscriptions are permanent.

## Temporal workflows

- `InitialWorkspaceScanWorkflow`: bounded first scan after connection; creates first operational state/Today.
- `CommitmentLifecycleWorkflow`: waits for attention windows, reevaluates, surfaces, snoozes, dismisses, or resolves.
- `HandleCommitmentWorkflow`: context, plan, safe steps, approval wait, execution, verification, and state update.
- `FollowUpWorkflow`: waits for external response; ends early on response or prepares an approved follow-up.
- `MeetingPreparationWorkflow`: gathers event, attendees, objective, commitments, relevant mail/docs, and prepares a briefing.
- `GoogleWatchRenewalWorkflow`: creates/renews provider watches/subscriptions and updates expiration state.

Temporal owns durable execution; PostgreSQL owns user-facing operational truth. Signals resume workflows for approval/rejection/modification, external responses, commitment completion, and agent pause.

## Security architecture

**Security invariant:** Content can influence NavoX’s understanding. Content cannot grant NavoX authority.

Authority hierarchy:

```text
SYSTEM SECURITY POLICY -> USER-GRANTED PERMISSIONS -> EXPLICIT USER APPROVAL
-> AGENT PLAN -> EXTERNAL CONTENT
```

- Models never receive raw Google credentials and never call providers directly.
- The Policy Engine validates every proposed tool action against the Permission Store.
- The Approval Engine authorizes exact actions; the Tool Gateway alone executes them.
- Email, documents, web pages, attachments, and messages are untrusted content and must be defended against prompt injection.
- Exact-action approval hashes the canonical security-relevant payload; a material mutation invalidates approval, and approvals expire.
- Webhook/event ownership derives from an authenticated provider connection, never a client-supplied user ID.
- All tenant-owned queries are workspace scoped; knowledge of an object UUID is never authorization.
- Audit events are append-only at the application level.
- Pause NavoX disables proactive writes/execution and prevents pending approvals from unexpectedly executing.

Security evaluations cover prompt injection, cross-tenant access, approval tampering or bypass, tool escalation, webhook replay, duplicate execution, OAuth scopes, malformed model output, data exfiltration, and agent loops.

## Data model contractor baseline

```text
users(id, email, display_name, timezone, agent_paused, created_at, updated_at)
workspaces(id, name, type, created_at, updated_at)
workspace_memberships(workspace_id, user_id, role, created_at)
connections(id, user_id, workspace_id, provider, external_account_id, status,
            granted_scopes[], credential_reference, last_sync_at, watch_expires_at, timestamps)
objectives(id, workspace_id, user_id, title, description, status, priority, target_at,
           created_by, timestamps, completed_at)
people(id, workspace_id, user_id, display_name, email, relationship, timestamps)
commitments(id, workspace_id, user_id, objective_id, type, title, description, status,
            priority, due_at, remind_at, confidence, created_by, valid_from, valid_until,
            last_verified_at, timestamps, completed_at)
commitment_sources(id, commitment_id, connection_id, provider, source_type,
                   external_resource_id, extracted_at, source_metadata JSONB)
commitment_people(commitment_id, person_id, role)
commitment_relations(id, from_commitment_id, to_commitment_id, relation_type, created_at)
memories(id, workspace_id, user_id, memory_type, subject_type, subject_id, key, value JSONB,
         confidence, source_type, source_reference, valid_from, valid_until,
         last_verified_at, timestamps)
plans(id, workspace_id, user_id, objective_id, commitment_id, goal, status,
      max_steps=8, replan_count, timestamps)
plan_steps(id, plan_id, sequence_number, action_type, description, status,
           input JSONB, output JSONB, timestamps)
actions(id, workspace_id, user_id, plan_step_id, commitment_id, provider, action_type,
        risk_level, requires_approval, status, payload JSONB, payload_hash, result JSONB,
        created_at, executed_at)
approvals(id, action_id, user_id, action_payload_hash, status, expires_at,
          approved_at, rejected_at, created_at)
notifications(id, workspace_id, user_id, commitment_id, type, attention_score, status,
              scheduled_for, surfaced_at, dismissed_at, created_at)
workflow_refs(id, workspace_id, user_id, entity_type, entity_id, workflow_type,
              temporal_workflow_id UNIQUE, temporal_run_id, status, timestamps)
incoming_events(id, workspace_id, user_id, connection_id, provider, event_type,
                external_event_id, external_resource_id, status, occurred_at,
                received_at, processed_at, metadata JSONB)
audit_events(id, workspace_id, user_id, event_type, actor_type, actor_id,
             entity_type, entity_id, metadata JSONB, occurred_at)
```

Implementation uses migrations, indexes, foreign keys, workspace boundaries, controlled enums/constants, timestamps, deletion policies, and idempotency constraints. OAuth refresh tokens are never casual plaintext columns; connection records reference protected secret storage.

## API direction

All routes begin at `/api/v1`:

```text
/auth/google
/connections
/today
/objectives
/commitments
/commitments/{id}/handle
/plans/{id}
/actions/{id}/approve
/actions/{id}/reject
/actions/{id}/edit
/agent/messages
/events/gmail
/events/calendar
/events/drive
/activity
```

REST initiates or queries work; it never synchronously runs a long-lived agent. Operations with external effects require idempotency protection. Today returns structured sections/items, reasons, attention scores, and suggested capabilities. Agent chat routes intent to structured state/context rather than inventing answers from chat history.

## Repository architecture

```text
navox/
├── apps/
│   ├── web/
│   └── extension/              # Phase 2
├── services/
│   └── api/
│       └── navox/
│           ├── api/
│           ├── agent/
│           ├── commitments/
│           ├── context/
│           ├── proactive/
│           ├── policy/
│           ├── approvals/
│           ├── connectors/
│           ├── ai/
│           └── db/
├── packages/
│   ├── contracts/
│   └── ui/
├── workflows/temporal/
├── evals/{commitments,planning,briefing,security}/
├── docs/{architecture,adr,security,api}/
├── infrastructure/
├── scripts/
├── .github/workflows/
├── .env.example
├── docker-compose.yml
├── README.md
└── LICENSE
```

## Delivery phases and milestones

| Phase | Outcome |
| --- | --- |
| 0 Engineering Foundation | Repository, ADRs, monorepo, Next.js/FastAPI skeletons, PostgreSQL, Temporal, local Docker, migrations, CI, environment policy, health checks, and fresh-developer setup. |
| 1 Identity and Google | Authentication, personal workspace creation, least-privilege Google OAuth, protected credential references, and connection health. No AI until identity, tenancy, and access are correct. |
| 2 Event Pipeline | Authenticated provider events, normalization, `incoming_events`, deduplication/idempotency, processors, malformed/duplicate tests. |
| 3 Commitment Engine | Structured AI extraction, validation, confidence policy, deduplication, provenance, relations, and malicious/false-positive evaluation fixtures. |
| 4 First NavoX Moment / Today | Needs Attention, Coming Up, Money/Renewals, Waiting On; one state powers dashboard and Today chat. |
| 5 Bounded Agent | Context Builder, planner, persisted plans/steps, policy validation, Temporal handle workflow, safe R0/R1 execution, progress UI. |
| 6 Approval and Execution | Gmail send as R3: prepare, persist, approve, payload-hash verify, execute, verify, audit, update commitment; bypass/duplicate/tamper tests. |
| 7 Proactive NavoX | Attention engine, meeting prep, deadline warnings, renewals, promised follow-ups, waiting-on responses, daily briefing, fatigue suppression. |
| 8 Chrome Extension | Only after the web loop is reliable. Side panel uses the same backend and no second agent architecture. |
| 9 Evaluation and Hardening | Intelligence, reliability, security, and product-quality evaluation; compare providers using data rather than demos. |

## Development and provenance

The repository is named `NavoX`. Development follows: design -> commit -> implement a small feature -> test -> commit -> push -> next. The history demonstrates development activity but is not cryptographic proof of sole authorship; represent AI assistance accurately if asked.

Never rewrite/polish history to make development look different from what happened. Never commit secrets. `.env.example` contains placeholders only. Enable CI and security scanning. The repository begins private; public visibility remains a user decision after a security, setup, demo, license, and secret-history review.

## Evaluation

| Metric | Evaluation |
| --- | --- |
| Commitment precision | How often surfaced/extracted commitments are genuinely commitments; track false positives. |
| Commitment recall | Whether important deadlines, promises, follow-ups, and renewals are missed in evaluation sets. |
| Briefing usefulness | User-marked usefulness/actionability; track dismissed/ignored items and repeated low-value alerts. |
| Notification fatigue | Rate of suppressed/dismissed/repeated alerts and preference changes. |
| Action success | Percentage of approved actions executed and independently verified successfully. |
| Approval integrity | No execution when the payload materially differs from approved payload; no approval bypass in security tests. |
| Security | Prompt injection, tool escalation, cross-tenant access, webhook replay, and data-exfiltration tests fail closed. |
| Reliability | Temporal recovery, retries, duplicate event handling, provider outage behavior, idempotent effects. |
| Latency | Time to Today response, commitment processing, plan creation, and approved-action completion. |
| Cost | AI/API/infrastructure cost per active user and successful operational outcome. |
| Model quality | Structured-output reliability, extraction accuracy, plan/tool selection, latency, cost, and security behavior by provider. |

## Definition of MVP complete

The MVP loop is complete when a user connects Google, NavoX discovers relevant operational context, extracts commitments, answers “What do I need to know today?” with something genuinely useful, then supports **Handle this** by gathering context, creating a bounded plan, preparing an action, requesting approval, executing the approved exact action, verifying success, updating operational state, and reflecting the new reality in future Today briefings.

That loop is NavoX. Teams, additional integrations, mobile, and broader autonomy wait until it is excellent.
