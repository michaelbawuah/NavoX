import { describe, expect, it } from "vitest";
import {
  appendMemorySelection,
  memoryInstruction,
  type PersonalMemory,
  readPersonalMemories,
} from "./personal-memory";

const preference: PersonalMemory = {
  id: "length",
  kind: "reply_length",
  label: "Reply length",
  content: "short",
  enabled: true,
  provenance: "user_saved",
  created_at: "2026-10-17T07:00:00Z",
  updated_at: "2026-10-17T07:00:00Z",
};

describe("explicit saved context", () => {
  it("adds only selected, enabled memories and leaves other private facts out", () => {
    const rows: PersonalMemory[] = [
      preference,
      {
        ...preference,
        id: "disabled",
        kind: "project_fact",
        content: "Disabled fact",
        enabled: false,
      },
      {
        ...preference,
        id: "unselected",
        kind: "project_fact",
        content: "Unselected private fact",
      },
    ];
    const result = appendMemorySelection(
      "Help me write a reply.",
      rows,
      ["length", "length", "disabled"],
      500,
    );
    expect(result.error).toBeUndefined();
    expect(result.value).toContain("Help me write a reply.");
    expect(result.value).toContain("Keep replies short.");
    expect(result.value).not.toContain("private fact");
    expect(result.value).not.toContain("Disabled fact");
    expect(result.value.match(/Keep replies short/g)).toHaveLength(1);
  });
  it("preserves the original question exactly when the complete selection cannot fit", () => {
    const value = `  ${"x".repeat(490)}  `;
    const result = appendMemorySelection(
      value,
      [preference],
      [preference.id],
      500,
    );
    expect(result.value).toBe(value);
    expect(result.error).toContain("Choose fewer preferences");
  });
  it("supports the exact limit without silently clipping a saved fact", () => {
    const first = appendMemorySelection(
      "Question",
      [preference],
      [preference.id],
      4000,
    );
    expect(
      appendMemorySelection(
        "Question",
        [preference],
        [preference.id],
        first.value.length,
      ),
    ).toEqual(first);
    expect(
      appendMemorySelection(
        "Question",
        [preference],
        [preference.id],
        first.value.length - 1,
      ).value,
    ).toBe("Question");
  });
  it("does not duplicate an unchanged block or apply a forgotten selection", () => {
    const first = appendMemorySelection(
      "Question",
      [preference],
      [preference.id],
      500,
    );
    expect(
      appendMemorySelection(first.value, [preference], [preference.id], 500),
    ).toEqual(first);
    expect(
      appendMemorySelection("Question", [], [preference.id], 500).error,
    ).toContain("Choose");
  });
  it("makes typed writing preferences readable in the existing visible instructions", () => {
    expect(
      memoryInstruction({
        ...preference,
        kind: "tone",
        content: "professional",
      }),
    ).toBe("Use a professional tone.");
    expect(
      memoryInstruction({
        ...preference,
        kind: "signoff",
        content: "Best, Michael",
      }),
    ).toContain("Best, Michael");
    expect(
      memoryInstruction({
        ...preference,
        kind: "project_fact",
        label: "My project",
        content: "NavoX",
      }),
    ).toBe("My project: NavoX");
  });
  it("rejects unknown provenance or an invalid API shape instead of pretending there are no memories", () => {
    expect(readPersonalMemories([preference])).toEqual([preference]);
    expect(() => readPersonalMemories({ items: [] })).toThrow(
      "could not be read",
    );
    expect(() =>
      readPersonalMemories([{ ...preference, provenance: "email_inferred" }]),
    ).toThrow();
    expect(() =>
      readPersonalMemories(Array.from({ length: 31 }, () => preference)),
    ).toThrow();
  });
});
