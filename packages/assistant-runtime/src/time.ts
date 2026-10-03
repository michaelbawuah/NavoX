import type {
  AssistantBlock,
  AssistantResponseState,
  CapabilityDecision,
} from "@navox/contracts";
import { parseCapabilityDecision } from "./validate";

/**
 * A direct read-only utility, not a delegation.
 *
 * The runtime reads its own injected clock and the validated session/request
 * IANA timezone. It never calls an external provider or a source service, and
 * it never turns another fact (a class or meeting time) into the current time.
 *
 * A missing or unusable timezone is answered from UTC with an explicit label.
 * Guessing the operator's locale would be less honest than naming the zone the
 * clock reading actually came from.
 */
export const FALLBACK_TIMEZONE = "UTC";

function isUsableTimezone(value: string | null): value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > 64) {
    return false;
  }
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

function decision(reason: string): CapabilityDecision {
  return parseCapabilityDecision({
    kind: "DELEGATE",
    capability_id: "time.now",
    target: "runtime.clock",
    reason,
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: "READY",
  });
}

export function answerCurrentTime(
  now: Date,
  timezone: string | null,
): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  const requested = isUsableTimezone(timezone) ? timezone : null;
  const zone = requested ?? FALLBACK_TIMEZONE;
  const formatted = new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(now);
  const text =
    requested === null
      ? `It is ${formatted}. Your timezone is missing or invalid, so this reading is labeled as the UTC fallback and is not necessarily your local time.`
      : `It is ${formatted} (${zone}).`;
  return {
    state: "READY",
    decision: decision(
      requested === null ? "time.now.utc_fallback" : "time.now.local",
    ),
    blocks: [{ kind: "ANSWER", text }],
  };
}
