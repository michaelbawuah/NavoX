import type {
  AssistantBlock,
  AssistantDeliveryIntent,
  AssistantModality,
  AssistantPresentation,
  AssistantPresentationPlan,
  CapabilityDecision,
} from "@navox/contracts";
import { LIMITS } from "./limits";
import { clampText, parsePresentationPlan } from "./validate";

function normalizeLine(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

/**
 * Splits one text into whole sentences. The split keeps its terminator so the
 * summary never cuts a sentence mid-thought, and a text with no terminator is
 * one sentence.
 */
function sentences(value: string): string[] {
  const normalized = normalizeLine(value);
  if (!normalized) return [];
  const parts = normalized.match(/[^.!?]+[.!?]*\s*/g);
  if (!parts) return [normalized];
  return parts.map((part) => part.trim()).filter((part) => part.length > 0);
}

/**
 * The ordered spoken candidates of one saved response. Only text the owning
 * service already produced is used, so the summary can never invent a fact.
 * Blocks are read in their saved order, so an interleaved answer, detail or
 * notice keeps the sequence the operator sees on screen.
 */
function speechLines(blocks: AssistantBlock[]): string[] {
  const lines: string[] = [];
  for (const block of blocks) {
    if (block.kind === "ANSWER") {
      lines.push(...sentences(block.text));
    } else if (block.kind === "DETAILS") {
      for (const line of block.lines) {
        lines.push(...sentences(line));
      }
    } else if (block.kind === "NOTICE") {
      lines.push(...sentences(block.text));
    }
  }
  return lines;
}

/**
 * A concise, coherent spoken summary derived only from a saved response.
 *
 * Whole sentences from the answer, details and notice are added in reading
 * order, following the saved block sequence, while they fit inside `max`;
 * nothing is paraphrased and nothing new is written. A single over-long
 * sentence falls back to a word-boundary cut, and only a pathological unbroken
 * string is hard-truncated at the bound.
 */
export function summarizeForSpeech(
  blocks: AssistantBlock[],
  max: number = LIMITS.maxSpeechLength,
): string | null {
  const lines = speechLines(blocks);
  if (lines.length === 0) return null;
  let summary = "";
  for (const line of lines) {
    const candidate = summary ? `${summary} ${line}` : line;
    if (candidate.length <= max) {
      summary = candidate;
      continue;
    }
    if (summary) break;
    summary = cutAtWordBoundary(line, max);
    break;
  }
  const trimmed = summary.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** The longest prefix that fits, preferring a word boundary when one exists. */
function cutAtWordBoundary(value: string, max: number): string {
  if (value.length <= max) return value;
  const prefix = value.slice(0, max);
  const boundary = prefix.lastIndexOf(" ");
  return boundary > 0 ? prefix.slice(0, boundary) : prefix;
}

/**
 * Whether a response rendered a sentence worth reading. A turn that rendered
 * only items, links or citations has nothing to speak.
 */
export function hasSpokenBlock(blocks: AssistantBlock[]): boolean {
  return blocks.some(
    (block) =>
      (block.kind === "ANSWER" || block.kind === "NOTICE") &&
      block.text.trim().length > 0,
  );
}

/**
 * The bounded spoken text of one response, or null when speech is not
 * eligible. An approval-required or non-ready answer never produces one; the
 * answer is still rendered on screen.
 */
export function speechSummaryForDecision(
  decision: CapabilityDecision,
  blocks: AssistantBlock[],
): string | null {
  // Speech is only a rendered offer, never authority: an answer that would
  // need approval is shown on screen and stays silent.
  if (decision.requires_approval) return null;
  if (
    decision.response_state !== "READY" &&
    decision.response_state !== "CLARIFY"
  )
    return null;
  // Only a response with an answer or a notice is speakable, matching the
  // long-standing offer rule: a turn that rendered only items or links has no
  // sentence to read.
  if (!hasSpokenBlock(blocks)) return null;
  return summarizeForSpeech(blocks, LIMITS.maxSpeechLength);
}

/**
 * Decides how one saved response is surfaced. The two inputs are the submitted
 * modality and the operator's delivery preference; neither carries authority.
 *
 * - `SUPPRESS` keeps the full visual answer and never speaks.
 * - `SPEAK` speaks the answer it belongs to, in a typed or spoken turn.
 * - A spoken turn speaks by default; a typed turn stays silent unless the
 *   operator asked otherwise.
 */
export function decideResponseModality(input: {
  modality: AssistantModality;
  delivery: AssistantDeliveryIntent;
  speakable: boolean;
}): AssistantPresentation {
  if (!input.speakable) return "TEXT";
  if (input.delivery === "SUPPRESS") return "TEXT";
  if (input.delivery === "SPEAK") return "BOTH";
  return input.modality === "VOICE" ? "BOTH" : "TEXT";
}

/**
 * Decides rendering and speech for one saved response.
 *
 * `speech_text` is stored when playback may start by itself. A silent turn
 * keeps only its visual blocks; the saved-turn speech route derives the same
 * coherent summary from those blocks, so an explicit Read aloud control never
 * needs a second stored copy of the answer.
 */
export function buildPresentationPlan(input: {
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
  modality: AssistantModality;
  delivery: AssistantDeliveryIntent;
}): AssistantPresentationPlan {
  const summary = speechSummaryForDecision(input.decision, input.blocks);
  const presentation = decideResponseModality({
    modality: input.modality,
    delivery: input.delivery,
    speakable: summary !== null,
  });
  const speak = presentation !== "TEXT" && summary !== null;
  return parsePresentationPlan({
    presentation,
    speak,
    speech_text:
      speak && summary !== null
        ? clampText(summary, LIMITS.maxSpeechLength)
        : null,
    delivery: input.delivery,
    blocks: input.blocks,
  });
}
