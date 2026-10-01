import type { AssistantDeliveryIntent } from "@navox/contracts";

export type { AssistantDeliveryIntent };

/**
 * Bounded delivery-preference detection for one utterance.
 *
 * This is deliberately not a route grammar: it never names a capability, it
 * never decides what the assistant may do, and it cannot suppress or trigger a
 * delegation. It only recognises whether the operator asked for the answer to
 * be read aloud, asked for it to stay silent, or said nothing about delivery.
 * Every general question still reaches the SPEC-005 planner unchanged, and the
 * utterance is never rewritten, so upstream question spans stay exact.
 */
export interface DeliveryResolution {
  intent: AssistantDeliveryIntent;
  /**
   * True when the utterance is only the delivery cue and carries no question
   * of its own. A cue-only request is answered from the saved conversation and
   * never reaches the planner.
   */
  cue_only: boolean;
  /**
   * A 1-based position in this conversation's saved turns, or `-1` for the
   * most recent. `null` means the operator named no specific turn.
   */
  ordinal: number | null;
}

/**
 * Substring cues that reliably express a delivery request wherever they occur
 * in the utterance. They are grouped by family so a suppression cue always
 * wins over the read-aloud phrase it contains.
 */
const SUPPRESS_MARKERS = [
  // Each cue names the answer it suppresses, so a general sentence such as
  // "I don't read the news much" is not mistaken for an instruction.
  "don't read it",
  "don't read that",
  "don't read this",
  "dont read it",
  "dont read that",
  "dont read this",
  "do not read it",
  "do not read that",
  "do not read this",
  "no need to read it",
  "without reading it",
  "don't say it",
  "don't say that",
  "dont say it",
  "do not say it",
  "don't speak it",
  "do not speak it",
  "keep it silent",
  "stay silent",
  "no voice",
  "read it silently",
  "no reading aloud",
] as const;

const SPEAK_MARKERS = [
  "read it to me",
  "read that to me",
  "read this to me",
  "read it aloud",
  "read that aloud",
  "read this aloud",
  "read it out loud",
  "read that out loud",
  "read this out loud",
  "read it back to me",
  "read it for me",
  "read it again",
  "read the last answer",
  "read me the last answer",
  "read the last one",
  "read the latest answer",
  "read the most recent answer",
  "read the most recent one",
  "read the previous answer",
  "read the first one",
  "read the second one",
  "read the third one",
  "read the first answer",
  "read the second answer",
  "read the third answer",
  "read that one to me",
  "read this one to me",
  "say it out loud",
  "say that again",
  "say that out loud",
  "speak it to me",
  "speak that to me",
  "read aloud",
] as const;

/**
 * Short read-aloud forms that only mean a delivery request on their own. They
 * are never substring cues: "did I read it yesterday" is an ordinary question.
 */
const SPEAK_ONLY = new Set([
  "read it",
  "read that",
  "read this",
  "speak it",
  "say it",
]);

/** Whole-utterance cues, checked after polite and filler words are removed. */
const CUE_ONLY = new Set([
  ...SPEAK_MARKERS,
  ...SPEAK_ONLY,
  "read the first one",
  "read the second one",
  "read the third one",
  "read the last one",
  "read the first answer",
  "read the second answer",
  "read the third answer",
  ...SUPPRESS_MARKERS,
  "don't read it",
  "dont read it",
  "do not read it",
  "don't read it to me",
  "don't read that",
  "don't read this",
  "don't say it out loud",
  "don't say that out loud",
  "don't speak it",
  "no need to read it",
  "no need to read it aloud",
  "without reading it aloud",
  "without reading it",
]);

const LEADING_FILLERS = [
  "please",
  "hey navox",
  "hey",
  "navox",
  "okay",
  "ok",
  "could you",
  "can you",
  "would you",
  "will you",
  "i want you to",
  "i would like you to",
  "i'd like you to",
  "just",
] as const;

const TRAILING_FILLERS = [
  "please",
  "now",
  "for me",
  "to me",
  "back to me",
  "out loud",
  "aloud",
  "thanks",
  "thank you",
  "okay",
  "ok",
] as const;

const ORDINALS: readonly (readonly [string, number])[] = [
  ["first one", 1],
  ["second one", 2],
  ["third one", 3],
  ["last one", -1],
  ["latest one", -1],
  ["most recent one", -1],
  ["previous one", -1],
  ["first answer", 1],
  ["second answer", 2],
  ["third answer", 3],
  ["last answer", -1],
  ["latest answer", -1],
  ["most recent answer", -1],
  ["previous answer", -1],
];

/** Lowercase, straight-quote, whitespace-collapsed comparison text. */
export function normalizeUtterance(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\u2018\u2019\u02bc`]/g, "'")
    .replace(/[^a-z0-9'\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * The same normalization, but clause punctuation survives as its own token.
 * A cue phrase only counts when it starts a clause, so a statement such as
 * "He read it to me" is not mistaken for an instruction.
 */
function normalizeWithBoundaries(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\u2018\u2019\u02bc`]/g, "'")
    .replace(/[^a-z0-9'\s.,;?!]/g, " ")
    .replace(/\s*([.,;?!])\s*/g, " $1 ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Words that may introduce an imperative cue inside a longer utterance. They
 * keep natural phrasing ("and read it to me", "could you read it aloud") working
 * without accepting a cue that is really the object of a statement.
 */
const LEAD_INS = new Set([
  "and",
  "then",
  "also",
  "but",
  "so",
  "or",
  "please",
  "now",
  "ok",
  "okay",
  "hey",
  "navox",
  "just",
  "actually",
  "could",
  "can",
  "would",
  "will",
  "you",
]);

function atClauseStart(boundary: string, index: number): boolean {
  if (index === 0) return true;
  const before = boundary.slice(0, index).trimEnd();
  const last = before.at(-1);
  if (last !== undefined && ".,;?!".includes(last)) return true;
  const previous = before.split(" ").filter(Boolean).at(-1) ?? "";
  return LEAD_INS.has(previous);
}

/** True when a cue phrase appears where an instruction can plausibly start. */
function containsCue(boundary: string, markers: readonly string[]): boolean {
  for (const marker of markers) {
    let from = 0;
    for (;;) {
      const index = boundary.indexOf(marker, from);
      if (index < 0) break;
      if (atClauseStart(boundary, index)) return true;
      from = index + 1;
    }
  }
  return false;
}

function stripFillers(normalized: string): string {
  let text = normalized;
  let changed = true;
  while (changed) {
    changed = false;
    for (const filler of LEADING_FILLERS) {
      if (text === filler) return "";
      if (text.startsWith(`${filler} `)) {
        text = text.slice(filler.length + 1).trim();
        changed = true;
      }
    }
    for (const filler of TRAILING_FILLERS) {
      if (text.endsWith(` ${filler}`)) {
        text = text.slice(0, -(filler.length + 1)).trim();
        changed = true;
      }
    }
  }
  return text;
}

function ordinalFrom(normalized: string): number | null {
  for (const [phrase, ordinal] of ORDINALS) {
    if (normalized.includes(phrase)) return ordinal;
  }
  return null;
}

/**
 * Classifies one utterance's delivery preference. Suppression is checked first
 * because "don't read it to me" contains a read-aloud phrase; a suppression
 * cue therefore always wins.
 */
export function resolveDeliveryIntent(text: string): DeliveryResolution {
  const normalized = normalizeUtterance(text);
  const stripped = stripFillers(normalized);
  const cueOnly = CUE_ONLY.has(stripped);
  const boundary = normalizeWithBoundaries(text);
  if (containsCue(boundary, SUPPRESS_MARKERS)) {
    return { intent: "SUPPRESS", cue_only: cueOnly, ordinal: null };
  }
  if (containsCue(boundary, SPEAK_MARKERS) || SPEAK_ONLY.has(stripped)) {
    return {
      intent: "SPEAK",
      cue_only: cueOnly,
      ordinal: cueOnly ? ordinalFrom(normalized) : null,
    };
  }
  return { intent: "AUTOMATIC", cue_only: false, ordinal: null };
}
