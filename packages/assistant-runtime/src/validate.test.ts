import { describe, expect, it } from "vitest";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";
import {
  assertUuid,
  isIanaTimezone,
  parseAssistantBlock,
  parseAssistantMessageRequest,
  parseCapabilityDecision,
  parseIntentPlan,
  parsePresentationPlan,
} from "./validate";

const requestId = "77777777-7777-4777-8777-777777777777";

function expectInvalid(run: () => unknown, fragment?: string): void {
  try {
    run();
    throw new Error("expected an invalid_request failure");
  } catch (error) {
    expect(error).toBeInstanceOf(AssistantError);
    const assistant = error as AssistantError;
    expect(assistant.code).toBe("invalid_request");
    if (fragment) expect(assistant.message).toContain(fragment);
  }
}

describe("assistant request validation", () => {
  it("accepts the four client-authored fields plus a timezone", () => {
    const parsed = parseAssistantMessageRequest({
      request_id: requestId,
      text: "  What am I missing today?  ",
      modality: "VOICE",
      timezone: "America/New_York",
      referents: ["task-a"],
    });
    expect(parsed).toEqual({
      request_id: requestId,
      text: "What am I missing today?",
      modality: "VOICE",
      timezone: "America/New_York",
      referents: ["task-a"],
    });
  });

  it.each([
    ["workspace_id", "22222222-2222-4222-8222-222222222222"],
    ["user_id", "11111111-1111-4111-8111-111111111111"],
    ["provider", "openai"],
    ["tool_name", "send_email"],
    ["action_grant", "approve-1"],
    ["trusted_source", "gmail"],
  ])("refuses client authority field %s", (field, value) => {
    expectInvalid(
      () =>
        parseAssistantMessageRequest({
          request_id: requestId,
          text: "What am I missing today?",
          modality: "TEXT",
          [field]: value,
        }),
      "does not accept",
    );
  });

  it("refuses a non-uuid request id, empty text and unknown modality", () => {
    expectInvalid(() =>
      parseAssistantMessageRequest({
        request_id: "not-a-uuid",
        text: "hi",
        modality: "TEXT",
      }),
    );
    expectInvalid(() =>
      parseAssistantMessageRequest({
        request_id: requestId,
        text: "   ",
        modality: "TEXT",
      }),
    );
    expectInvalid(() =>
      parseAssistantMessageRequest({
        request_id: requestId,
        text: "hi",
        modality: "AUDIO",
      }),
    );
  });

  it("bounds text, referents and timezone", () => {
    expectInvalid(() =>
      parseAssistantMessageRequest({
        request_id: requestId,
        text: "x".repeat(LIMITS.maxQuestionLength + 1),
        modality: "TEXT",
      }),
    );
    expectInvalid(() =>
      parseAssistantMessageRequest({
        request_id: requestId,
        text: "hi",
        modality: "TEXT",
        referents: Array.from(
          { length: LIMITS.maxReferents + 1 },
          (_, index) => `${index}`,
        ),
      }),
    );
    expectInvalid(() =>
      parseAssistantMessageRequest({
        request_id: requestId,
        text: "hi",
        modality: "TEXT",
        timezone: "Mars/Olympus",
      }),
    );
  });

  it("recognises real IANA zones only", () => {
    expect(isIanaTimezone("UTC")).toBe(true);
    expect(isIanaTimezone("America/New_York")).toBe(true);
    expect(isIanaTimezone("not a zone")).toBe(false);
    expect(isIanaTimezone("")).toBe(false);
    expect(isIanaTimezone(7)).toBe(false);
  });

  it("asserts uuids without accepting v9-style placeholders", () => {
    expect(assertUuid(requestId, "request ID")).toBe(requestId);
    expect(() =>
      assertUuid("00000000-0000-0000-0000-000000000000", "request ID"),
    ).toThrow();
    expect(() => assertUuid(null, "request ID")).toThrow();
  });
});

describe("plan and decision validation", () => {
  it("rejects an unknown route or capability", () => {
    expectInvalid(() =>
      parseIntentPlan({
        version: 1,
        intents: [
          {
            kind: "send.email",
            capability_id: "email.send",
            question: "hi",
            confidence: 1,
          },
        ],
      }),
    );
    expectInvalid(() =>
      parseIntentPlan({
        version: 1,
        intents: [
          {
            kind: "today.read",
            capability_id: "today.write",
            question: "hi",
            confidence: 1,
          },
        ],
      }),
    );
  });

  it("rejects an unknown plan version and over-long plans", () => {
    expectInvalid(() => parseIntentPlan({ version: 2, intents: [] }));
    expectInvalid(() =>
      parseIntentPlan({
        version: 1,
        intents: Array.from({ length: LIMITS.maxIntents + 1 }, () => ({
          kind: "today.read",
          capability_id: "today.read",
          question: "hi",
          confidence: 1,
        })),
      }),
    );
  });

  it("rejects unknown decision kinds, action states and response states", () => {
    const base = {
      capability_id: "today.read",
      target: "today.query",
      reason: "today.today",
      requires_approval: false,
      action_state: "NONE",
      action_id: null,
      response_state: "READY",
    };
    expectInvalid(() => parseCapabilityDecision({ ...base, kind: "EXECUTE" }));
    expectInvalid(() =>
      parseCapabilityDecision({
        ...base,
        kind: "DELEGATE",
        action_state: "RUNNING",
      }),
    );
    expectInvalid(() =>
      parseCapabilityDecision({
        ...base,
        kind: "DELEGATE",
        response_state: "OK",
      }),
    );
    expect(
      parseCapabilityDecision({ ...base, kind: "DELEGATE" }).response_state,
    ).toBe("READY");
  });
});

describe("block and presentation validation", () => {
  it("rejects unknown block kinds and unbounded detail lines", () => {
    expectInvalid(() =>
      parseAssistantBlock({ kind: "SCRIPT", text: "<script>" }),
    );
    expectInvalid(() =>
      parseAssistantBlock({
        kind: "DETAILS",
        lines: Array.from({ length: LIMITS.maxDetails + 1 }, () => "line"),
      }),
    );
    expect(parseAssistantBlock({ kind: "ANSWER", text: "ok" })).toEqual({
      kind: "ANSWER",
      text: "ok",
    });
  });

  it("never accepts a speaking plan with nothing to say", () => {
    expectInvalid(() =>
      parsePresentationPlan({
        presentation: "VOICE",
        speak: true,
        speech_text: null,
        blocks: [],
      }),
    );
    expect(
      parsePresentationPlan({
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        blocks: [],
      }),
    ).toEqual({
      presentation: "TEXT",
      speak: false,
      speech_text: null,
      blocks: [],
    });
  });
});

describe("intent plan slots", () => {
  const legacyIntent = {
    kind: "today.read",
    capability_id: "today.read",
    question: "What is today?",
    confidence: 1,
  };

  it("defaults absent slots for plans stored before M2", () => {
    const plan = parseIntentPlan(
      { version: 1, intents: [legacyIntent] },
      {
        allowBoundTurn: true,
      },
    );
    expect(plan.intents[0]?.entity).toEqual({
      kind: "NONE",
      value: null,
      confidence: 1,
    });
    expect(plan.intents[0]?.time).toEqual({
      kind: "NONE",
      expression: null,
      confidence: 1,
    });
    expect(plan.intents[0]?.reference).toEqual({
      kind: "NONE",
      ordinal: null,
      turn_id: null,
    });
    expect(plan.intents[0]?.requires_clarification).toBe(false);
    expect(plan.intents[0]?.clarification).toBeNull();
  });

  it("refuses an upstream turn selector unless the runtime bound it", () => {
    const bound = {
      ...legacyIntent,
      reference: {
        kind: "RECENT_TURN",
        ordinal: 1,
        turn_id: requestId,
      },
    };
    expectInvalid(() => parseIntentPlan({ version: 1, intents: [bound] }));
    expect(
      parseIntentPlan(
        { version: 1, intents: [bound] },
        {
          allowBoundTurn: true,
        },
      ).intents[0]?.reference.turn_id,
    ).toBe(requestId);
  });

  it("refuses unknown or oversized slot values", () => {
    expectInvalid(() =>
      parseIntentPlan({
        version: 1,
        intents: [
          {
            ...legacyIntent,
            entity: { kind: "PLANET", value: "Mars", confidence: 1 },
          },
        ],
      }),
    );
    expectInvalid(() =>
      parseIntentPlan({
        version: 1,
        intents: [
          {
            ...legacyIntent,
            time: {
              kind: "ABSOLUTE",
              expression: "x".repeat(LIMITS.maxIntentSlotLength + 1),
              confidence: 1,
            },
          },
        ],
      }),
    );
    expectInvalid(() =>
      parseIntentPlan({
        version: 1,
        intents: [
          {
            ...legacyIntent,
            kind: "assistant.clarify",
            capability_id: null,
            requires_clarification: true,
            clarification: "x".repeat(LIMITS.maxClarificationLength + 1),
          },
        ],
      }),
    );
    expectInvalid(() =>
      parseIntentPlan({
        version: 1,
        intents: [
          {
            ...legacyIntent,
            reference: { kind: "RECENT_TURN", ordinal: null, turn_id: null },
          },
        ],
      }),
    );
  });
});
