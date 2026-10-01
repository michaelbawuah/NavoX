import type {
  AssistantBlock,
  AssistantResponseState,
  CapabilityDecision,
  PlannedIntent,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { isRecord, isUuid, parseCapabilityDecision } from "./validate";

interface SubscriptionRecord {
  id: string;
  revision: number;
  name: string;
  plan_name: string | null;
  status: string;
  next_renewal_at: string | null;
  last_verified_at: string | null;
  updated_at: string;
}

interface CancellationRecord {
  status: string;
  verification_status: string;
  estimated_access_end: string | null;
}

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "Subscriptions returned information this runtime could not verify.",
  );
}

function bounded(value: unknown, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max)
    unreadable();
  return value;
}

function optionalText(value: unknown, max: number): string | null {
  if (value === null) return null;
  return bounded(value, max);
}

function optionalDate(value: unknown): string | null {
  if (value === null) return null;
  const date = bounded(value, 64);
  if (
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(date) ||
    !Number.isFinite(Date.parse(date))
  )
    unreadable();
  return date;
}

function boundedRevision(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1)
    unreadable();
  return value;
}

/** A model-proposed entity must be an exact span of the user's own question. */
export function subscriptionSelector(intent: PlannedIntent): string | null {
  const value = intent.entity.value?.trim() ?? "";
  if (
    intent.kind !== "subscription.search" ||
    intent.entity.kind === "NONE" ||
    value.length < 2 ||
    value.length > 200 ||
    !intent.question
      .toLocaleLowerCase("en-US")
      .includes(value.toLocaleLowerCase("en-US"))
  )
    return null;
  return value;
}

export function parseSubscriptionSearch(
  payload: unknown,
  selector: string,
): SubscriptionRecord[] {
  if (
    !isRecord(payload) ||
    payload.intent !== "SEARCH" ||
    !Array.isArray(payload.subscriptions) ||
    payload.subscriptions.length > 50
  )
    unreadable();
  const seen = new Set<string>();
  return payload.subscriptions.map((entry) => {
    if (!isRecord(entry) || !isUuid(entry.id) || seen.has(entry.id))
      unreadable();
    seen.add(entry.id);
    const name = bounded(entry.name, 256);
    const plan = optionalText(entry.plan_name, 256);
    if (
      !`${name} ${plan ?? ""}`
        .toLocaleLowerCase("en-US")
        .includes(selector.toLocaleLowerCase("en-US"))
    )
      unreadable();
    return {
      id: entry.id,
      revision: boundedRevision(entry.revision),
      name,
      plan_name: plan,
      status: bounded(entry.status, 64),
      next_renewal_at: optionalDate(entry.next_renewal_at),
      last_verified_at: optionalDate(entry.last_verified_at),
      updated_at: optionalDate(entry.updated_at) ?? unreadable(),
    };
  });
}

export function parseSubscriptionCancellation(
  payload: unknown,
  subscription: SubscriptionRecord,
  scope: { user_id: string; workspace_id: string },
): CancellationRecord | null {
  if (payload === null) return null;
  if (
    !isRecord(payload) ||
    !isUuid(payload.id) ||
    payload.obligation_id !== subscription.id
  )
    unreadable();
  const preview = payload.preview;
  if (
    !isRecord(preview) ||
    !isRecord(preview.target) ||
    preview.target.obligation_id !== subscription.id ||
    preview.target.user_id !== scope.user_id ||
    preview.target.workspace_id !== scope.workspace_id ||
    preview.target.revision !== String(subscription.revision)
  )
    unreadable();
  return {
    status: bounded(payload.status, 64),
    verification_status: bounded(payload.verification_status, 64),
    estimated_access_end: optionalDate(preview.access_ends_at),
  };
}

function decision(
  state: AssistantResponseState,
  reason: string,
): CapabilityDecision {
  return parseCapabilityDecision({
    kind: state === "READY" ? "DELEGATE" : "CLARIFY",
    capability_id: "subscription.search",
    target: "subscriptions.query",
    reason,
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: state,
  });
}

export function clarifySubscriptions(rows: SubscriptionRecord[]): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  const text =
    rows.length === 0
      ? "I could not find that subscription in your saved records. Which one did you mean?"
      : `I found ${rows.length} matching subscriptions. Which one did you mean?`;
  return {
    state: "CLARIFY",
    decision: decision(
      "CLARIFY",
      rows.length === 0 ? "subscription.no_match" : "subscription.ambiguous",
    ),
    blocks: [
      { kind: "ANSWER", text },
      ...rows.slice(0, 5).map(
        (row): AssistantBlock => ({
          kind: "ITEM",
          item: {
            id: row.id,
            type: "SUBSCRIPTION",
            title: row.name,
            description: row.plan_name,
            status: row.status,
            due_at: row.next_renewal_at,
            band: "SUBSCRIPTION",
            sources: [
              {
                provider: "navox",
                source_type: "SUBSCRIPTION",
                external_resource_id: null,
                evidence_id: row.id,
                connection_id: null,
                observed_at: row.updated_at,
              },
            ],
          },
        }),
      ),
    ],
  };
}

export function answerSubscription(
  row: SubscriptionRecord,
  cancellation: CancellationRecord | null,
): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  const cancelled =
    cancellation?.status === "VERIFIED_CANCELLED" &&
    cancellation.verification_status === "VERIFIED_CANCELLED";
  const renewal = row.next_renewal_at
    ? `Recorded renewal date: ${row.next_renewal_at}.`
    : "No renewal date is recorded.";
  const answer = cancelled
    ? `${row.name}: cancellation is verified. ${renewal} The access end date is unknown.`
    : `${row.name}: ${renewal} Cancellation is not verified. The access end date is unknown.`;
  const lines = [
    `Saved status: ${row.status}.`,
    cancellation
      ? `Cancellation verification: ${cancellation.verification_status}.`
      : "No cancellation attempt is recorded.",
    "Access end date: unknown.",
  ];
  if (cancellation?.estimated_access_end) {
    lines.push(
      `Provider preview estimated access end: ${cancellation.estimated_access_end}. This is not verified.`,
    );
  }
  if (row.last_verified_at)
    lines.push(`Subscription last verified: ${row.last_verified_at}.`);
  return {
    state: "READY",
    decision: decision("READY", "subscription.found_one"),
    blocks: [
      { kind: "ANSWER", text: answer },
      {
        kind: "ITEM",
        item: {
          id: row.id,
          type: "SUBSCRIPTION",
          title: row.name,
          description: row.plan_name,
          status: row.status,
          due_at: row.next_renewal_at,
          band: "SUBSCRIPTION",
          sources: [
            {
              provider: "navox",
              source_type: "SUBSCRIPTION",
              external_resource_id: null,
              evidence_id: row.id,
              connection_id: null,
              observed_at: row.updated_at,
            },
          ],
        },
      },
      { kind: "DETAILS", lines },
    ],
  };
}
