import type {
  AssistantBlock,
  AssistantCitation,
  AssistantItemBlock,
  AssistantResponseState,
  CapabilityDecision,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";
import {
  clampText,
  isRecord,
  isUuid,
  parseCapabilityDecision,
  parseCitation,
} from "./validate";

/**
 * The read-only SPEC-007 resource types this route may resolve. A response that
 * names anything else is a qualified failure, never a plausible answer.
 */
export const EMAIL_SOURCE_TYPES = ["EMAIL", "EMAIL_THREAD"] as const;

export type EmailSourceType = (typeof EMAIL_SOURCE_TYPES)[number];

/** One exact evidence selector. Excerpt text is counted, never copied. */
export interface EmailEvidence {
  resource_id: string;
  source_type: EmailSourceType;
  title: string | null;
  source_version: string | null;
  source_updated_at: string | null;
  fresh_until: string | null;
  indexed_at: string | null;
  connection_id: string | null;
  external_resource_id: string | null;
  excerpt_count: number;
}

export interface EmailSearchOutcome {
  results: EmailEvidence[];
  truncated: boolean;
  unavailable_modes: number;
  source_issues: number;
  partial_reasons: string[];
  suggested_followups: string[];
  examined: number;
}

export interface EmailSearchDecision {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
}

function unverifiable(): never {
  throw new AssistantError(
    "unavailable",
    "Connected search returned something this runtime could not verify.",
  );
}

function optionalText(value: unknown, max: number): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") unverifiable();
  return clampText(value, max);
}

function requiredBoundedText(value: unknown, max: number): string {
  if (typeof value !== "string") unverifiable();
  return clampText(value, max);
}

/**
 * Validates a SPEC-007 `/search/query` response before anything is trusted.
 * Coverage, freshness and exact selectors survive; excerpt text does not.
 */
export function parseEmailSearchOutcome(payload: unknown): EmailSearchOutcome {
  if (!isRecord(payload)) unverifiable();
  const { results, coverage, unavailable_modes: unavailableModes } = payload;
  if (!Array.isArray(results) || results.length > LIMITS.maxItems)
    unverifiable();
  const parsed = results.map((entry): EmailEvidence => {
    if (!isRecord(entry)) unverifiable();
    const sourceType = entry.source_type;
    if (sourceType !== "EMAIL" && sourceType !== "EMAIL_THREAD") {
      unverifiable();
    }
    const resourceId = entry.resource_id;
    if (typeof resourceId !== "string" || !isUuid(resourceId)) unverifiable();
    const provenance = isRecord(entry.provenance) ? entry.provenance : {};
    const excerpts = entry.excerpts ?? [];
    if (!Array.isArray(excerpts)) unverifiable();
    return {
      resource_id: resourceId,
      source_type: sourceType,
      title: optionalText(entry.title, 500),
      source_version: optionalText(entry.source_version, 256),
      source_updated_at: optionalText(entry.source_updated_at, 64),
      fresh_until: optionalText(entry.fresh_until, 64),
      indexed_at: optionalText(entry.indexed_at, 64),
      connection_id: optionalText(provenance.connection_id, 128),
      external_resource_id: optionalText(provenance.external_resource_id, 512),
      excerpt_count: excerpts.length,
    };
  });
  if (!isRecord(coverage)) unverifiable();
  if (typeof coverage.truncated !== "boolean") unverifiable();
  const examined = coverage.examined ?? 0;
  if (
    typeof examined !== "number" ||
    !Number.isFinite(examined) ||
    examined < 0
  ) {
    unverifiable();
  }
  const modes = unavailableModes ?? [];
  if (!Array.isArray(modes)) unverifiable();
  const sourceIssues = coverage.source_issues ?? [];
  if (!Array.isArray(sourceIssues)) unverifiable();
  const partialReasons = coverage.partial_reasons ?? [];
  if (
    !Array.isArray(partialReasons) ||
    partialReasons.some((reason) => typeof reason !== "string")
  ) {
    unverifiable();
  }
  const followups = payload.suggested_followups ?? [];
  if (!Array.isArray(followups) || followups.length > LIMITS.maxSuggestions) {
    unverifiable();
  }
  return {
    results: parsed,
    truncated: coverage.truncated,
    unavailable_modes: modes.length,
    source_issues: sourceIssues.length,
    partial_reasons: partialReasons.map((reason) =>
      requiredBoundedText(reason, 120),
    ),
    suggested_followups: followups.map((query) =>
      requiredBoundedText(query, 200),
    ),
    examined,
  };
}

/**
 * Honest currentness label derived from the owning service's own metadata. An
 * absent `fresh_until` is unknown freshness, not a claim that the source is
 * current, so it stays UNVERIFIED.
 */
function currentness(result: EmailEvidence, now: Date): string {
  if (result.source_version === null) return "UNVERIFIED";
  if (result.fresh_until === null) return "UNVERIFIED";
  const fresh = Date.parse(result.fresh_until);
  if (!Number.isFinite(fresh)) return "UNVERIFIED";
  return fresh <= now.getTime() ? "STALE" : "CURRENT";
}

function citationFor(result: EmailEvidence): AssistantCitation {
  return parseCitation({
    provider: "navox",
    source_type: result.source_type,
    external_resource_id: result.external_resource_id,
    evidence_id: result.resource_id,
    connection_id: result.connection_id,
    observed_at: result.source_updated_at,
  });
}

function itemFor(result: EmailEvidence, now: Date): AssistantItemBlock {
  return {
    id: result.resource_id,
    type: result.source_type,
    title: clampText(result.title ?? "Untitled email", 300),
    description: null,
    status: currentness(result, now),
    due_at: null,
    band: "CONNECTED",
    sources: [citationFor(result)],
  };
}

function decisionFor(input: {
  kind: "DELEGATE" | "CLARIFY";
  reason: string;
  responseState: AssistantResponseState;
}): CapabilityDecision {
  return parseCapabilityDecision({
    kind: input.kind,
    capability_id: "email.search",
    target: "knowledge.search",
    reason: input.reason,
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: input.responseState,
  });
}

/**
 * Turns a validated read-only search into either one grounded answer or an
 * honest clarification. Multiple matches are never collapsed into a guess, and
 * nothing here can execute, send or approve anything.
 */
export function decideEmailSearch(
  outcome: EmailSearchOutcome,
  options?: { now?: Date },
): EmailSearchDecision {
  const now = options?.now ?? new Date();
  const results = outcome.results.slice(0, LIMITS.maxEmailResults);
  const items = results.map((result) => itemFor(result, now));
  const citations = items
    .flatMap((item) => item.sources)
    .slice(0, LIMITS.maxCitations);
  // Incomplete coverage cannot establish a unique target: the owning service
  // has said that candidates may be missing, so even one result is a
  // clarification with a caveat.
  const incomplete =
    outcome.truncated ||
    outcome.unavailable_modes > 0 ||
    outcome.source_issues > 0 ||
    outcome.partial_reasons.length > 0;
  const details: string[] = [];
  if (incomplete) {
    details.push("Search coverage was incomplete, so other matches may exist.");
  }
  if (outcome.source_issues > 0) {
    details.push("Some connected sources could not be searched.");
  }
  if (items.length > 0 && items.some((item) => item.status !== "CURRENT")) {
    details.push("A matching source may have changed since it was indexed.");
  }
  const followups = outcome.suggested_followups
    .slice(0, LIMITS.maxSuggestions)
    .map((query) => clampText(query, 200));

  if (items.length === 0) {
    const blocks: AssistantBlock[] = [
      {
        kind: "NOTICE",
        state: "CLARIFY",
        text: "I could not find a matching email in your connected sources. Nothing was changed.",
      },
    ];
    if (details.length > 0) blocks.push({ kind: "DETAILS", lines: details });
    if (followups.length > 0)
      blocks.push({ kind: "SUGGESTIONS", queries: followups });
    return {
      state: "CLARIFY",
      decision: decisionFor({
        kind: "CLARIFY",
        reason: "email.search.no_match",
        responseState: "CLARIFY",
      }),
      blocks,
    };
  }

  if (items.length === 1) {
    if (incomplete) {
      const blocks: AssistantBlock[] = [
        {
          kind: "ANSWER",
          text: "I found one possible matching email, but the search coverage was incomplete.",
        },
        { kind: "ITEM", item: items[0] as AssistantItemBlock },
      ];
      if (details.length > 0) blocks.push({ kind: "DETAILS", lines: details });
      if (citations.length > 0) blocks.push({ kind: "CITATIONS", citations });
      return {
        state: "CLARIFY",
        decision: decisionFor({
          kind: "CLARIFY",
          reason: "email.search.incomplete",
          responseState: "CLARIFY",
        }),
        blocks,
      };
    }
    const blocks: AssistantBlock[] = [
      {
        kind: "ANSWER",
        text: "I found one matching email in your connected sources.",
      },
      { kind: "ITEM", item: items[0] as AssistantItemBlock },
    ];
    if (details.length > 0) blocks.push({ kind: "DETAILS", lines: details });
    if (citations.length > 0) blocks.push({ kind: "CITATIONS", citations });
    return {
      state: "READY",
      decision: decisionFor({
        kind: "DELEGATE",
        reason: "email.search.found_one",
        responseState: "READY",
      }),
      blocks,
    };
  }

  const answer = incomplete
    ? `I found ${items.length} possible matching emails, but the search coverage was incomplete. Which one did you mean?`
    : `I found ${items.length} matching emails. Which one did you mean?`;
  const blocks: AssistantBlock[] = [
    { kind: "ANSWER", text: answer },
    ...items.map((item): AssistantBlock => ({ kind: "ITEM", item })),
  ];
  if (details.length > 0) blocks.push({ kind: "DETAILS", lines: details });
  if (citations.length > 0) blocks.push({ kind: "CITATIONS", citations });
  return {
    state: "CLARIFY",
    decision: decisionFor({
      kind: "CLARIFY",
      reason: incomplete ? "email.search.incomplete" : "email.search.ambiguous",
      responseState: "CLARIFY",
    }),
    blocks,
  };
}

/** Explicitly chosen, still-authorized message from a fresh complete search. */
export function decideEmailSelection(
  outcome: EmailSearchOutcome,
  resourceId: string,
  options?: { now?: Date },
): EmailSearchDecision {
  if (
    outcome.truncated ||
    outcome.unavailable_modes > 0 ||
    outcome.source_issues > 0 ||
    outcome.partial_reasons.length > 0
  ) {
    throw new AssistantError(
      "conflict",
      "Email search coverage changed. Search again before selecting.",
    );
  }
  const matches = outcome.results.filter(
    (result) => result.resource_id === resourceId,
  );
  if (matches.length !== 1 || matches[0]?.source_type !== "EMAIL") {
    throw new AssistantError(
      "conflict",
      "That exact email is no longer available. Search again.",
    );
  }
  return decideEmailSearch({ ...outcome, results: matches }, options);
}
