# Milestone 7: Proactive NavoX

SPEC-001 remains authoritative. Milestone 7 turns saved operational state into
deterministic, fatigue-aware proactive intelligence. It does not grant new
authority to models or external content.

## Product loop

```text
commitments + objectives + event state
  -> deterministic proactive evaluation
  -> scored, deduplicated signals
  -> fatigue / quiet-hour policy
  -> notify-now tier OR briefing tier OR dashboard tier OR suppress
  -> user dismisses / snoozes / handles
  -> state changes
  -> next evaluation reflects the new reality
```

Every surfaced signal must answer:

1. What is happening?
2. Why does it matter?
3. What can NavoX do about it?

## Initial proactive signal types

Milestone 7 derives signals from persisted NavoX commitments:

- deadline warnings;
- upcoming meetings and meeting preparation;
- renewals;
- promised follow-ups;
- waiting-on-response aging;
- high-priority undated work;
- candidate commitments needing review.

No Gmail, Calendar, or Drive read scope is added merely to make proactive
screens look richer. Provider content remains unavailable until a later
least-privilege read capability is intentionally introduced. Existing
commitment provenance remains visible.

## Attention score

The engine implements the SPEC formula as deterministic components:

```text
attention score =
  urgency
  + consequence
  + user priority
  + objective relevance
  + actionability
  + waiting duration
  - interruption cost
  - notification fatigue
```

The score is clamped to 0..100. Components and reasons are persisted so a user
or evaluator can explain every result. Thresholds are versioned policy values
stored in per-user proactive preferences rather than hidden model behavior.

Default tiers:

- 85..100: `notify_now`
- 65..84: `briefing`
- 40..64: `dashboard`
- below 40: `suppressed`

These are evaluation parameters, not claims of universal optimality.

## Fatigue controls

Defaults:

- quiet hours: 22:00 to 07:00 local time;
- at most three interruptive surfaces per local day;
- four-hour cooldown before the same signal can interrupt again.

Quiet hours and fatigue reduce interruption tier but never delete the
underlying operational state. Critical overdue work remains visible in Today
and briefing views even when an interruption is suppressed.

Users can dismiss a signal or snooze it until a chosen timestamp. Completion,
rejection, expiry, or invalidity of the underlying commitment resolves the
signal.

## Persistence

`proactive_signals` stores deterministic signal identity, score components,
tier, user suppression state, and surfacing history.

`proactive_preferences` stores user-controlled thresholds, quiet hours,
cooldown, daily briefing hour, and interruption budget.

`briefing_snapshots` stores only signal IDs plus a content hash for audit and
evaluation. The live daily briefing is always regenerated from current state;
a stale snapshot never controls what the user sees.

`commitments.waiting_since` records the beginning of a waiting-on-external
period so follow-up aging does not depend on incidental row updates.

## Durable workflows

Temporal owns timers and recovery. Milestone 7 adds:

- `CommitmentLifecycleWorkflow` for periodic reevaluation;
- `FollowUpWorkflow` for waiting-on-external aging;
- `MeetingPreparationWorkflow` for bounded pre-meeting preparation;
- `DailyBriefingWorkflow` for durable daily briefing readiness.

PostgreSQL remains user-facing truth. Temporal may wake work; it cannot grant
provider permissions or action approval.

## Safety boundary

Milestone 7 may read NavoX state, calculate scores, prepare briefing text, and
offer existing capabilities. It does not autonomously execute R2+ actions.
Consequential provider actions still flow through Milestone 6 exact-action
approval.

When NavoX is globally paused, proactive evaluation may keep internal state
fresh, but no interruptive delivery or external execution is allowed.

## Evaluation

Tests cover:

- score determinism and boundary thresholds;
- timezone and DST behavior;
- deadline, meeting, renewal, promise, and waiting aging signals;
- deduplication and tenant isolation;
- completion resolving stale signals;
- dismiss and snooze behavior;
- quiet hours, cooldown, and daily interruption budgets;
- dynamic briefing freshness;
- meeting-prep selection;
- Temporal timer recovery and idempotent reevaluation;
- no external provider execution from proactive workflows.
