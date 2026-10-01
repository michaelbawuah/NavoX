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
  isTodayIntent,
  parseCapabilityDecision,
  parseCitation,
  type TodayIntent,
} from "./validate";

export interface TodayQueryResult {
  intent: TodayIntent;
  answer: string;
  items: AssistantItemBlock[];
  supported_queries: string[];
  details: string[];
}

function upstreamInvalid(): never {
  throw new AssistantError(
    "unavailable",
    "Today returned an answer this runtime could not verify.",
  );
}

/**
 * Validates a SPEC-002 `/today/query` payload before anything is trusted.
 * A malformed or oversized payload becomes a qualified failure; the runtime
 * never repairs it into a plausible answer.
 */
export function parseTodayQueryResult(payload: unknown): TodayQueryResult {
  if (!isRecord(payload)) upstreamInvalid();
  const {
    intent,
    answer,
    items,
    supported_queries: supportedQueries,
    details,
  } = payload;
  // The intent vocabulary is owned by SPEC-002. An unknown value is a qualified
  // failure, never an assumed success.
  if (!isTodayIntent(intent)) {
    upstreamInvalid();
  }
  if (typeof answer !== "string" || answer.length > LIMITS.maxAnswerLength)
    upstreamInvalid();
  // SPEC-002 always answers a supported intent with text; an empty answer is an
  // inconsistent payload, so it must not become a READY turn with no answer.
  if (intent !== "unsupported" && answer.trim().length === 0) upstreamInvalid();
  if (!Array.isArray(items) || items.length > LIMITS.maxItems)
    upstreamInvalid();
  const itemBlocks: AssistantItemBlock[] = items.map((item) => {
    if (!isRecord(item)) upstreamInvalid();
    const sources = item.sources ?? [];
    if (!Array.isArray(sources) || sources.length > LIMITS.maxCitations)
      upstreamInvalid();
    const description = item.description ?? null;
    const dueAt = item.due_at ?? null;
    const band = item.band ?? null;
    if (description !== null && typeof description !== "string")
      upstreamInvalid();
    if (dueAt !== null && typeof dueAt !== "string") upstreamInvalid();
    if (band !== null && typeof band !== "string") upstreamInvalid();
    if (typeof item.id !== "string" || typeof item.type !== "string")
      upstreamInvalid();
    if (typeof item.title !== "string" || typeof item.status !== "string")
      upstreamInvalid();
    return {
      id: clampText(item.id, 128),
      type: clampText(item.type, 64),
      title: clampText(item.title, 300),
      description: description === null ? null : clampText(description, 1000),
      status: clampText(item.status, 64),
      due_at: dueAt as string | null,
      band: band as string | null,
      sources: (sources as unknown[]).map(parseCitation) as AssistantCitation[],
    };
  });
  const suggestions = supportedQueries ?? [];
  if (!Array.isArray(suggestions) || suggestions.length > LIMITS.maxSuggestions)
    upstreamInvalid();
  const lines = details ?? [];
  if (!Array.isArray(lines) || lines.length > LIMITS.maxDetails)
    upstreamInvalid();
  if (lines.some((line) => typeof line !== "string")) upstreamInvalid();
  if (suggestions.some((query) => typeof query !== "string")) upstreamInvalid();
  return {
    intent,
    answer: clampText(answer, LIMITS.maxAnswerLength),
    items: itemBlocks,
    supported_queries: (suggestions as string[]).map((query) =>
      clampText(query, 200),
    ),
    details: (lines as string[]).map((line) => clampText(line, 400)),
  };
}

/** SPEC-002 owns classification; this only translates its answer into a decision. */
export function decideToday(result: TodayQueryResult): CapabilityDecision {
  if (result.intent === "unsupported") {
    return parseCapabilityDecision({
      kind: "CLARIFY",
      capability_id: null,
      target: "today.query",
      reason: "today.unsupported",
      requires_approval: false,
      action_state: "NONE",
      action_id: null,
      response_state: "CLARIFY",
    });
  }
  return parseCapabilityDecision({
    kind: "DELEGATE",
    capability_id: "today.read",
    target: "today.query",
    reason: `today.${result.intent}`,
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: "READY",
  });
}

export function noticeBlocks(
  state: AssistantResponseState,
  text: string,
): AssistantBlock[] {
  return [
    { kind: "NOTICE", state, text: clampText(text, LIMITS.maxAnswerLength) },
  ];
}

/** Typed structured blocks for an answer. No third-party evidence text is copied. */
export function blocksFromToday(result: TodayQueryResult): AssistantBlock[] {
  const blocks: AssistantBlock[] = [];
  if (result.answer) blocks.push({ kind: "ANSWER", text: result.answer });
  for (const item of result.items) blocks.push({ kind: "ITEM", item });
  if (result.details.length > 0)
    blocks.push({ kind: "DETAILS", lines: result.details });
  const citations = result.items
    .flatMap((item) => item.sources)
    .slice(0, LIMITS.maxCitations);
  if (citations.length > 0) blocks.push({ kind: "CITATIONS", citations });
  if (result.intent === "unsupported" && result.supported_queries.length > 0) {
    blocks.push({ kind: "SUGGESTIONS", queries: result.supported_queries });
  }
  if (blocks.length === 0) {
    // Only reachable for an unsupported intent with nothing else to show, so the
    // notice state must match `decideToday` rather than a blanket UNAVAILABLE.
    blocks.push(
      ...noticeBlocks(
        result.intent === "unsupported" ? "CLARIFY" : "UNAVAILABLE",
        "Today returned no answer for this question.",
      ),
    );
  }
  return blocks;
}
