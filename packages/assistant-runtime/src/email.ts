import type {
  AssistantBlock,
  AssistantCitation,
  AssistantItemBlock,
  AssistantResponseState,
  AssistantSourceExcerpt,
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

/**
 * Why one exact email's current content could not be presented.
 *
 * `changed` means the revision moved since the search; `stale` means the
 * owning service's freshness bound passed; `no_content` means the index has no
 * readable chunk; `unverified` means the revision or freshness metadata was
 * missing or unreadable. None of them is a reason to guess.
 */
export type EmailContentIssue =
  | "changed"
  | "stale"
  | "no_content"
  | "unverified";

/**
 * The verified content of one exact email, read from the current SPEC-007
 * resource detail. `THREAD_ONLY` means the match is a thread, which this
 * runtime must never present as one message; `UNAVAILABLE` means the current
 * content could not be verified as the same, unexpired revision the search
 * named, so the answer is qualified instead of guessed.
 */
export type EmailContent =
  | {
      state: "READY";
      excerpts: AssistantSourceExcerpt[];
      source_version: string;
      fresh_until: string;
    }
  | { state: "THREAD_ONLY" }
  | { state: "UNAVAILABLE"; reason: EmailContentIssue };

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
 * The one candidate whose verified content this turn may read, or null.
 *
 * A single hit inside incomplete coverage is not a unique target, and neither
 * is one hit when the owning service says another match may exist. Only a
 * complete, exactly-one-result search names content to read.
 */
export function emailContentTarget(outcome: EmailSearchOutcome): {
  resource_id: string;
  source_type: EmailSourceType;
  source_version: string | null;
  fresh_until: string | null;
} | null {
  if (
    outcome.truncated ||
    outcome.unavailable_modes > 0 ||
    outcome.source_issues > 0 ||
    outcome.partial_reasons.length > 0
  ) {
    return null;
  }
  const results = outcome.results.slice(0, LIMITS.maxEmailResults);
  if (results.length !== 1) return null;
  const only = results[0];
  return only
    ? {
        resource_id: only.resource_id,
        source_type: only.source_type,
        source_version: only.source_version,
        fresh_until: only.fresh_until,
      }
    : null;
}

/**
 * The same rule for an explicitly chosen candidate: one exact match inside a
 * complete search. A partial or ambiguous search names no readable content,
 * and the selection itself is still re-validated before it is used.
 */
export function selectedEmailTarget(
  outcome: EmailSearchOutcome,
  resourceId: string,
): {
  resource_id: string;
  source_type: EmailSourceType;
  source_version: string | null;
  fresh_until: string | null;
} | null {
  if (
    outcome.truncated ||
    outcome.unavailable_modes > 0 ||
    outcome.source_issues > 0 ||
    outcome.partial_reasons.length > 0
  ) {
    return null;
  }
  const matches = outcome.results.filter(
    (result) => result.resource_id === resourceId,
  );
  if (matches.length !== 1) return null;
  const only = matches[0];
  return only
    ? {
        resource_id: only.resource_id,
        source_type: only.source_type,
        source_version: only.source_version,
        fresh_until: only.fresh_until,
      }
    : null;
}

/** A bounded excerpt that never presents more than the owning service read. */
function boundExcerpt(value: string): string {
  if (value.length <= LIMITS.maxExcerptLength) return value;
  const prefix = value.slice(0, LIMITS.maxExcerptLength - 3);
  const boundary = prefix.lastIndexOf(" ");
  return `${(boundary > 0 ? prefix.slice(0, boundary) : prefix).trimEnd()}...`;
}

/** The exact identity and revision one content read must match. */
export interface EmailContentExpectation {
  resource_id: string;
  source_type: EmailSourceType;
  /** Version the search named; null means the search could not prove one. */
  source_version: string | null;
  /** Freshness bound the search named; null means the search could not prove one. */
  fresh_until: string | null;
}

/**
 * Validates one SPEC-007 resource detail for the exact candidate this turn is
 * answering about.
 *
 * The detail is a fresh current-source read: it re-checks workspace,
 * exclusions and the stored revision in the owning service. On top of that,
 * this runtime requires the detail to be the same revision the search named,
 * to carry an unexpired freshness bound, and to hold at least one readable
 * content chunk. A title, a changed revision, a stale or unknown bound, or an
 * empty index is qualified rather than presented as the message.
 */
export function parseEmailResourceDetail(
  payload: unknown,
  expected: EmailContentExpectation,
  options: { now: Date },
): EmailContent {
  if (!isRecord(payload)) unverifiable();
  if (
    payload.resource_id !== expected.resource_id ||
    payload.source_type !== expected.source_type
  ) {
    unverifiable();
  }
  if (expected.source_type !== "EMAIL") return { state: "THREAD_ONLY" };
  const version = payload.source_version;
  if (
    typeof version !== "string" ||
    version.trim().length === 0 ||
    version.length > 256
  ) {
    return { state: "UNAVAILABLE", reason: "unverified" };
  }
  if (expected.source_version === null || expected.source_version !== version) {
    return { state: "UNAVAILABLE", reason: "changed" };
  }
  const freshUntil = payload.fresh_until;
  if (typeof freshUntil !== "string" || freshUntil.length === 0) {
    return { state: "UNAVAILABLE", reason: "unverified" };
  }
  const freshAt = Date.parse(freshUntil);
  if (!Number.isFinite(freshAt)) {
    return { state: "UNAVAILABLE", reason: "unverified" };
  }
  // The detail must be the same indexed authority the search named: the exact
  // revision and the freshness bound the answer is about to rely on.
  if (expected.fresh_until === null || expected.fresh_until !== freshUntil) {
    return { state: "UNAVAILABLE", reason: "unverified" };
  }
  if (freshAt <= options.now.getTime()) {
    return { state: "UNAVAILABLE", reason: "stale" };
  }
  const rawChunks = payload.chunks ?? [];
  if (!Array.isArray(rawChunks) || rawChunks.length > 256) unverifiable();
  const excerpts: AssistantSourceExcerpt[] = [];
  for (const raw of rawChunks) {
    if (excerpts.length >= LIMITS.maxExcerpts) break;
    if (!isRecord(raw)) unverifiable();
    const text = raw.text_content;
    if (text === null || text === undefined) continue;
    if (typeof text !== "string") unverifiable();
    const trimmed = text.replace(/\s+/g, " ").trim();
    if (trimmed.length === 0) continue;
    excerpts.push({ source: "content", text: boundExcerpt(trimmed) });
  }
  if (excerpts.length === 0) {
    return { state: "UNAVAILABLE", reason: "no_content" };
  }
  return {
    state: "READY",
    excerpts,
    source_version: version,
    fresh_until: freshUntil,
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

/** The stable reason each unverifiable content state maps to. */
const CONTENT_REASONS: Record<EmailContentIssue, string> = {
  changed: "email.search.changed",
  stale: "email.search.stale",
  no_content: "email.search.incomplete",
  unverified: "email.search.unverified",
};

/**
 * Turns a validated read-only search into either one grounded answer or an
 * honest clarification. Multiple matches are never collapsed into a guess, and
 * nothing here can execute, send or approve anything.
 */
export function decideEmailSearch(
  outcome: EmailSearchOutcome,
  options?: { now?: Date; content?: EmailContent },
): EmailSearchDecision {
  const now = options?.now ?? new Date();
  const content: EmailContent = options?.content ?? {
    state: "UNAVAILABLE",
    reason: "unverified",
  };
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
    const item = items[0] as AssistantItemBlock;
    // A thread is never one message: keep its citation and offer a safe path,
    // but never quote a thread listing or an individual message inferred from
    // a thread ID.
    if (item.type === "EMAIL_THREAD") {
      const blocks: AssistantBlock[] = [
        {
          kind: "ANSWER",
          text: "I found one matching conversation, but it is an email thread rather than a single message.",
        },
        { kind: "ITEM", item },
        {
          kind: "NOTICE",
          state: "CLARIFY",
          text: "I can't quote one message from a thread. Ask me to open that thread, or name the exact message.",
        },
      ];
      if (citations.length > 0) blocks.push({ kind: "CITATIONS", citations });
      return {
        state: "CLARIFY",
        decision: decisionFor({
          kind: "CLARIFY",
          reason: "email.search.thread_only",
          responseState: "CLARIFY",
        }),
        blocks,
      };
    }
    // Both the owning service's own freshness label and the fresh resource
    // detail must agree this is the same, unexpired revision before anything is
    // presented as the message. A title, an unknown or stale bound, a moved
    // revision and an empty index are all qualified instead of guessed.
    const hit = results[0] as EmailEvidence;
    const freshness = currentness(hit, now);
    const reason =
      freshness !== "CURRENT"
        ? freshness === "STALE"
          ? "email.search.stale"
          : "email.search.unverified"
        : content.state === "UNAVAILABLE"
          ? CONTENT_REASONS[content.reason]
          : content.state !== "READY"
            ? "email.search.thread_only"
            : null;
    if (reason !== null || content.state !== "READY") {
      const blocks: AssistantBlock[] = [
        {
          kind: "ANSWER",
          text: "I found one matching email, but I could not verify its indexed content as current.",
        },
        { kind: "ITEM", item },
        {
          kind: "NOTICE",
          state: "CLARIFY",
          text: "The message may have changed since it was indexed. Nothing was changed; search again before drafting a reply.",
        },
      ];
      if (details.length > 0) blocks.push({ kind: "DETAILS", lines: details });
      if (citations.length > 0) blocks.push({ kind: "CITATIONS", citations });
      return {
        state: "CLARIFY",
        decision: decisionFor({
          kind: "CLARIFY",
          reason: reason ?? "email.search.unverified",
          responseState: "CLARIFY",
        }),
        blocks,
      };
    }
    const blocks: AssistantBlock[] = [
      {
        kind: "ANSWER",
        text: "I found one matching email in your indexed sources.",
      },
      { kind: "ITEM", item },
      {
        kind: "EVIDENCE",
        evidence_id: item.id,
        source_type: item.type,
        source_version: content.source_version,
        fresh_until: content.fresh_until,
      },
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
  options?: { now?: Date; content?: EmailContent },
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
