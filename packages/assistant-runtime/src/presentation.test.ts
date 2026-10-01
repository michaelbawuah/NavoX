import type { AssistantBlock } from "@navox/contracts";
import { describe, expect, it } from "vitest";
import { LIMITS } from "./limits";
import { buildPresentationPlan, summarizeForSpeech } from "./presentation";
import { decideToday, parseTodayQueryResult } from "./today";

const answer = parseTodayQueryResult({
  intent: "today",
  answer: "2 items need attention now.",
  items: [],
  supported_queries: [],
  details: [],
});
const decision = decideToday(answer);
const blocks: AssistantBlock[] = [
  { kind: "ANSWER", text: "2 items need attention now." },
];

describe("presentation plan", () => {
  it("keeps a typed turn silent and visual when Voice Mode is off", () => {
    const plan = buildPresentationPlan({
      decision,
      blocks,
      modality: "TEXT",
      delivery: "AUTOMATIC",
    });
    expect(plan.presentation).toBe("TEXT");
    expect(plan.speak).toBe(false);
    expect(plan.speech_text).toBeNull();
    expect(plan.blocks).toHaveLength(1);
  });

  it("returns the same visual blocks plus a bounded spoken answer for a voice turn", () => {
    const plan = buildPresentationPlan({
      decision,
      blocks,
      modality: "VOICE",
      delivery: "AUTOMATIC",
    });
    expect(plan.presentation).toBe("BOTH");
    expect(plan.speak).toBe(true);
    expect(plan.speech_text).toBe("2 items need attention now.");
    expect(plan.blocks).toEqual(blocks);
  });

  it("speaks an answer the operator explicitly asked to hear", () => {
    const plan = buildPresentationPlan({
      decision,
      blocks,
      modality: "TEXT",
      delivery: "SPEAK",
    });
    expect(plan.presentation).toBe("BOTH");
    expect(plan.speak).toBe(true);
    expect(plan.speech_text).toBe("2 items need attention now.");
  });

  it("suppresses automatic speech for the answer it belongs to", () => {
    const plan = buildPresentationPlan({
      decision,
      blocks,
      modality: "VOICE",
      delivery: "SUPPRESS",
    });
    expect(plan.presentation).toBe("TEXT");
    expect(plan.speak).toBe(false);
    expect(plan.speech_text).toBeNull();
    expect(plan.blocks).toEqual(blocks);
  });

  it("stays silent when the answer is unavailable", () => {
    const plan = buildPresentationPlan({
      decision: {
        kind: "UNAVAILABLE",
        capability_id: "today.read",
        target: "today.query",
        reason: "today.unavailable",
        requires_approval: false,
        action_state: "NONE",
        action_id: null,
        response_state: "UNAVAILABLE",
      },
      blocks: [
        {
          kind: "NOTICE",
          state: "UNAVAILABLE",
          text: "Today is not reachable.",
        },
      ],
      modality: "VOICE",
      delivery: "AUTOMATIC",
    });
    expect(plan.presentation).toBe("TEXT");
    expect(plan.speak).toBe(false);
    expect(plan.speech_text).toBeNull();
  });

  it("never speaks a decision that would need approval", () => {
    const plan = buildPresentationPlan({
      decision: { ...decision, requires_approval: true },
      blocks,
      modality: "TEXT",
      delivery: "SPEAK",
    });
    expect(plan.speak).toBe(false);
    expect(plan.speech_text).toBeNull();
    // The answer is still rendered; only the spoken offer is withheld.
    expect(plan.blocks).toEqual(blocks);
  });

  it("clamps long speech to the spoken bound", () => {
    const plan = buildPresentationPlan({
      decision,
      blocks: [{ kind: "ANSWER", text: "x".repeat(LIMITS.maxAnswerLength) }],
      modality: "VOICE",
      delivery: "AUTOMATIC",
    });
    expect(plan.speak).toBe(true);
    expect(plan.speech_text).toHaveLength(LIMITS.maxSpeechLength);
  });
});

describe("spoken summary", () => {
  it("keeps whole sentences and stops before the bound", () => {
    const blocks: AssistantBlock[] = [
      { kind: "ANSWER", text: "First sentence. Second sentence." },
      { kind: "DETAILS", lines: ["A detail."] },
    ];
    expect(summarizeForSpeech(blocks, 20)).toBe("First sentence.");
    expect(summarizeForSpeech(blocks, 60)).toBe(
      "First sentence. Second sentence. A detail.",
    );
  });

  it("never cuts a compound answer mid-thought when a sentence fits", () => {
    const blocks: AssistantBlock[] = [
      {
        kind: "ANSWER",
        text: "Weather is cloudy. Today has two tasks. News has one story.",
      },
    ];
    // Whole sentences are kept; the third sentence does not fit inside 40.
    expect(summarizeForSpeech(blocks, 40)).toBe(
      "Weather is cloudy. Today has two tasks.",
    );
    // A bound too small for the first sentence keeps it whole.
    expect(summarizeForSpeech(blocks, 30)).toBe("Weather is cloudy.");
  });

  it("drops a partial word when a single sentence is over the bound", () => {
    const summary = summarizeForSpeech(
      [{ kind: "ANSWER", text: "one two three four five six" }],
      12,
    );
    expect(summary).toBe("one two");
  });

  it("keeps the saved block order instead of grouping by kind", () => {
    const interleaved: AssistantBlock[] = [
      { kind: "ANSWER", text: "First answer." },
      { kind: "DETAILS", lines: ["A detail."] },
      { kind: "NOTICE", state: "READY", text: "A notice." },
      { kind: "ANSWER", text: "Second answer." },
    ];
    expect(summarizeForSpeech(interleaved, 600)).toBe(
      "First answer. A detail. A notice. Second answer.",
    );
  });

  it("derives nothing from a response with no answer or notice", () => {
    expect(
      summarizeForSpeech([
        {
          kind: "ITEM",
          item: {
            id: "11111111-1111-4111-8111-111111111111",
            type: "EMAIL",
            title: "An email",
            description: null,
            status: "READY",
            due_at: null,
            band: null,
            sources: [],
          },
        },
      ]),
    ).toBeNull();
  });
});
