import type {
  AssistantCapabilityId,
  AssistantIntentKind,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { assertAssistantCapabilityId, INTENT_KINDS } from "./validate";

/**
 * One entry per route the runtime may delegate to. The registry is server-owned:
 * a plan names a capability ID, it never names an executable tool or a provider.
 *
 * This registry authorizes only delegated read-only routes so far. A
 * consequential route is only added with its SPEC-001 approval and SPEC-003
 * execution contract, and must set
 * `requires_approval` to true.
 */
export interface CapabilityDefinition {
  id: AssistantCapabilityId;
  version: "1";
  mode: "read_only" | "consequential";
  requires_approval: boolean;
  /** Stable delegation target inside the NavoX API. */
  delegate_target: string;
  description: string;
}

const definitions = [
  {
    id: "today.read",
    version: "1",
    mode: "read_only",
    requires_approval: false,
    delegate_target: "today.query",
    description:
      "Read the current SPEC-002 Today projection for the signed-in scope.",
  },
  {
    id: "email.search",
    version: "1",
    mode: "read_only",
    requires_approval: false,
    delegate_target: "knowledge.search",
    description:
      "Resolve an email, person or thread through read-only SPEC-007 search and evidence selectors.",
  },
  {
    id: "subscription.search",
    version: "1",
    mode: "read_only",
    requires_approval: false,
    delegate_target: "subscriptions.query",
    description:
      "Read the signed-in user's SPEC-004 subscription and cancellation status.",
  },
  {
    id: "news.read",
    version: "1",
    mode: "read_only",
    requires_approval: false,
    delegate_target: "news.trending",
    description:
      "Read current SPEC-006 trending stories and source-backed summaries.",
  },
  {
    id: "weather.read",
    version: "1",
    mode: "read_only",
    requires_approval: false,
    delegate_target: "workspace.weather",
    description:
      "Read the signed-in workspace's current city weather observation.",
  },
  {
    id: "class.next",
    version: "1",
    mode: "read_only",
    requires_approval: false,
    delegate_target: "knowledge.class-sources",
    description: "Read permission-fenced Canvas and Calendar class meetings.",
  },
  {
    id: "time.now",
    version: "1",
    mode: "read_only",
    requires_approval: false,
    // Not a network target: the runtime answers this route from its own clock.
    delegate_target: "runtime.clock",
    description:
      "Read the current time from the runtime's injected clock and validated timezone.",
  },
  {
    id: "action.history",
    version: "1",
    mode: "read_only",
    requires_approval: false,
    // The existing authenticated SPEC-001/003 GET /actions list, already
    // scoped by workspace and user. The runtime never duplicates action rules.
    delegate_target: "actions.query",
    description:
      "Read the signed-in user's recent action records from the existing action ledger.",
  },
] as const satisfies readonly CapabilityDefinition[];

/**
 * The complete route-to-capability mapping. A route with no capability is a
 * clarification route, never a delegation. Kept in lockstep with the shared
 * intent vocabulary so an unowned route cannot be delegated by accident.
 */
const ROUTE_CAPABILITIES: Record<
  AssistantIntentKind,
  AssistantCapabilityId | null
> = {
  "today.read": "today.read",
  "email.search": "email.search",
  "subscription.search": "subscription.search",
  "news.read": "news.read",
  "weather.read": "weather.read",
  "class.next": "class.next",
  "time.now": "time.now",
  "action.history": "action.history",
  // A delivery request only speaks an already-saved answer; it delegates to no
  // capability and never reaches the planner.
  "assistant.delivery": null,
  // A navigation request only points at an item an earlier turn already cited;
  // it delegates to no capability and never reaches the planner.
  "assistant.navigate": null,
  "assistant.clarify": null,
};

export const CAPABILITIES: readonly CapabilityDefinition[] =
  Object.freeze(definitions);

const byId = new Map<string, CapabilityDefinition>(
  CAPABILITIES.map((definition) => [definition.id, definition]),
);

export function listCapabilities(): readonly CapabilityDefinition[] {
  return CAPABILITIES;
}

/**
 * Resolves a planned route to its server-owned capability, or null when the
 * route is a clarification. An unowned route is a configuration error, not a
 * reason to guess a delegate target.
 */
export function capabilityForIntentKind(
  kind: AssistantIntentKind,
): CapabilityDefinition | null {
  if (!Object.hasOwn(ROUTE_CAPABILITIES, kind)) {
    throw new AssistantError(
      "misconfigured",
      `Route ${kind} has no capability registry entry.`,
    );
  }
  const capabilityId = ROUTE_CAPABILITIES[kind];
  if (capabilityId === null) return null;
  return resolveCapability(capabilityId);
}

/** Resolves a server-owned capability. Unknown IDs are refused, never guessed. */
export function resolveCapability(id: unknown): CapabilityDefinition {
  const capabilityId = assertAssistantCapabilityId(id);
  const definition = byId.get(capabilityId);
  if (!definition) {
    throw new AssistantError(
      "invalid_request",
      "That capability is not available to this runtime.",
    );
  }
  return definition;
}

/** Invariants the registry must hold before the runtime may delegate. */
export function assertRegistryIntegrity(): void {
  const seen = new Set<string>();
  for (const route of INTENT_KINDS) {
    if (!Object.hasOwn(ROUTE_CAPABILITIES, route)) {
      throw new AssistantError(
        "misconfigured",
        `Route ${route} has no capability registry entry.`,
      );
    }
    const capabilityId = ROUTE_CAPABILITIES[route];
    if (capabilityId !== null && !byId.has(capabilityId)) {
      throw new AssistantError(
        "misconfigured",
        `Route ${route} delegates to an unregistered capability.`,
      );
    }
  }
  for (const definition of CAPABILITIES) {
    if (seen.has(definition.id)) {
      throw new AssistantError(
        "misconfigured",
        `Duplicate capability ${definition.id}`,
      );
    }
    seen.add(definition.id);
    if (!definition.delegate_target.trim()) {
      throw new AssistantError(
        "misconfigured",
        `Capability ${definition.id} has no target`,
      );
    }
    if (definition.mode === "consequential" && !definition.requires_approval) {
      throw new AssistantError(
        "misconfigured",
        `Consequential capability ${definition.id} must require approval`,
      );
    }
    if (definition.mode === "read_only" && definition.requires_approval) {
      throw new AssistantError(
        "misconfigured",
        `Read-only capability ${definition.id} must not require approval`,
      );
    }
  }
}
