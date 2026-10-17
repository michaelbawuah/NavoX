import { describe, expect, it, vi } from "vitest";
import {
  consumeStudentClassDraft,
  createStudentPushToTalk,
  studentClassDraftKey,
} from "./student-voice";

function event(overrides: Record<string, unknown> = {}) {
  return {
    key: " ",
    code: "Space",
    target: null,
    preventDefault: vi.fn(),
    ...overrides,
  };
}

describe("hold-Space quick asks", () => {
  it("owns one start and finish across repeats and release over an input", () => {
    const controls = {
      canStart: () => true,
      start: vi.fn(),
      finish: vi.fn(),
      cancel: vi.fn(),
    };
    const keys = createStudentPushToTalk(controls);
    keys.keyDown(event());
    keys.keyDown(event({ repeat: true }));
    keys.keyDown(event());
    keys.keyUp(event({ target: { closest: () => true } }));
    keys.keyUp(event());
    expect(controls.start).toHaveBeenCalledTimes(1);
    expect(controls.finish).toHaveBeenCalledTimes(1);
    expect(controls.cancel).not.toHaveBeenCalled();
  });
  it.each([
    { ctrlKey: true },
    { metaKey: true },
    { altKey: true },
    { shiftKey: true },
    { repeat: true },
    { isComposing: true },
    { defaultPrevented: true },
    { target: { closest: () => true } },
    { target: { isContentEditable: true } },
  ])("preserves typing, controls and shortcuts", (overrides) => {
    const controls = {
      canStart: () => true,
      start: vi.fn(),
      finish: vi.fn(),
      cancel: vi.fn(),
    };
    const value = event(overrides);
    createStudentPushToTalk(controls).keyDown(value);
    expect(controls.start).not.toHaveBeenCalled();
    expect(value.preventDefault).not.toHaveBeenCalled();
  });
  it("blur/visibility cancellation cannot submit on a later keyup", () => {
    const controls = {
      canStart: () => true,
      start: vi.fn(),
      finish: vi.fn(),
      cancel: vi.fn(),
    };
    const keys = createStudentPushToTalk(controls);
    keys.keyDown(event());
    keys.cancel();
    keys.cancel();
    keys.keyUp(event());
    expect(controls.cancel).toHaveBeenCalledTimes(1);
    expect(controls.finish).not.toHaveBeenCalled();
  });
  it("never starts while busy or unsupported", () => {
    const controls = {
      canStart: () => false,
      start: vi.fn(),
      finish: vi.fn(),
      cancel: vi.fn(),
    };
    createStudentPushToTalk(controls).keyDown(event());
    expect(controls.start).not.toHaveBeenCalled();
  });
});

describe("explicit class-review handoff", () => {
  const now = Date.parse("2026-10-17T10:00:00Z");
  const account = "workspace:user";
  function storage(value: unknown) {
    return { getItem: vi.fn(() => JSON.stringify(value)), removeItem: vi.fn() };
  }
  it("consumes the matching account's bounded fresh note exactly once", () => {
    const source = storage({
      accountKey: account,
      transcript: "Add a class CS 2110",
      createdAt: new Date(now - 1000).toISOString(),
    });
    expect(consumeStudentClassDraft(source, account, now)).toBe(
      "Add a class CS 2110",
    );
    expect(source.getItem).toHaveBeenCalledWith(studentClassDraftKey(account));
    expect(source.removeItem).toHaveBeenCalledWith(
      studentClassDraftKey(account),
    );
  });
  it.each([
    {
      accountKey: "other",
      transcript: "Add a class",
      createdAt: new Date(now).toISOString(),
    },
    {
      accountKey: account,
      transcript: "x".repeat(501),
      createdAt: new Date(now).toISOString(),
    },
    {
      accountKey: account,
      transcript: "Add a class",
      createdAt: new Date(now - 600_000).toISOString(),
    },
    {
      accountKey: account,
      transcript: "Add a class",
      createdAt: new Date(now + 1).toISOString(),
    },
  ])(
    "discards foreign, stale or oversized data without opening a write",
    (value) =>
      expect(consumeStudentClassDraft(storage(value), account, now)).toBeNull(),
  );
});
