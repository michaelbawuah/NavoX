import { describe, expect, it } from "vitest";
import { answerCurrentTime } from "./time";

const NOW = new Date("2026-09-30T12:00:00.000Z");

function answerText(answer: ReturnType<typeof answerCurrentTime>): string {
  const block = answer.blocks.find((candidate) => candidate.kind === "ANSWER");
  return block && block.kind === "ANSWER" ? block.text : "";
}

describe("direct current-time capability", () => {
  it("answers from the injected clock in the validated IANA zone", () => {
    const answer = answerCurrentTime(NOW, "America/New_York");
    expect(answer.state).toBe("READY");
    expect(answer.decision).toMatchObject({
      kind: "DELEGATE",
      capability_id: "time.now",
      target: "runtime.clock",
      reason: "time.now.local",
      requires_approval: false,
      action_state: "NONE",
      action_id: null,
      response_state: "READY",
    });
    // 12:00 UTC is 8:00 AM in New York on this date (EDT, UTC-4).
    expect(answerText(answer)).toContain("8:00 AM");
    expect(answerText(answer)).toContain("America/New_York");
  });

  it("labels the UTC fallback instead of guessing the operator's locale", () => {
    for (const timezone of [
      null,
      "",
      "Mars/Olympus_Mons",
      "not a zone",
      "America/New_York ",
    ]) {
      const answer = answerCurrentTime(NOW, timezone as string | null);
      expect(answer.state).toBe("READY");
      expect(answer.decision.reason).toBe("time.now.utc_fallback");
      expect(answerText(answer)).toContain("12:00 PM");
      expect(answerText(answer)).toContain("UTC");
      expect(answerText(answer)).toMatch(/not necessarily your local time/i);
    }
  });

  it("keeps UTC when the caller actually named UTC", () => {
    const answer = answerCurrentTime(NOW, "UTC");
    expect(answer.decision.reason).toBe("time.now.local");
    expect(answerText(answer)).toContain("12:00 PM");
    expect(answerText(answer)).toContain("UTC");
    expect(answerText(answer)).not.toMatch(/missing or invalid/i);
  });

  it("carries no approval, action or inferred schedule", () => {
    const answer = answerCurrentTime(NOW, "America/New_York");
    expect(answer.decision.requires_approval).toBe(false);
    expect(answer.decision.action_state).toBe("NONE");
    expect(answer.decision.action_id).toBeNull();
    expect(answer.blocks.every((block) => block.kind === "ANSWER")).toBe(true);
    expect(answerText(answer)).not.toMatch(/class|approve|send|calendar/i);
  });
});
