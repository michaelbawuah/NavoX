import type { AssistantBlock } from "@navox/contracts";
import { describe, expect, it } from "vitest";
import { LIMITS } from "./limits";
import { buildPresentationPlan } from "./presentation";
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
  it("never speaks a typed turn", () => {
    const plan = buildPresentationPlan({
      decision,
      blocks,
      presentation: "TEXT",
    });
    expect(plan.speak).toBe(false);
    expect(plan.speech_text).toBeNull();
    expect(plan.blocks).toHaveLength(1);
  });

  it("speaks a ready answer for a clicked voice turn", () => {
    const plan = buildPresentationPlan({
      decision,
      blocks,
      presentation: "VOICE",
    });
    expect(plan.speak).toBe(true);
    expect(plan.speech_text).toBe("2 items need attention now.");
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
      presentation: "VOICE",
    });
    expect(plan.speak).toBe(false);
    expect(plan.speech_text).toBeNull();
  });

  it("never speaks a decision that would need approval", () => {
    const plan = buildPresentationPlan({
      decision: { ...decision, requires_approval: true },
      blocks,
      presentation: "BOTH",
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
      presentation: "BOTH",
    });
    expect(plan.speak).toBe(true);
    expect(plan.speech_text).toHaveLength(LIMITS.maxSpeechLength);
  });
});
