import { describe, expect, it } from "vitest";
import {
  STUDENT_VOICE_TTL_MS,
  StudentVoiceReceiptStore,
} from "./student-voice-server";

const scope = { user_id: "owner", workspace_id: "workspace" };
const input = {
  scope,
  sessionId: "session",
  lookup: { mode: "next" as const },
  text: "Your next class is CS 2110.",
  answerDigest: "a".repeat(64),
};

describe("private spoken-answer receipts", () => {
  it("binds every read/delete to both account and exact session", () => {
    const store = new StudentVoiceReceiptStore();
    const receipt = store.put(input);
    expect(() =>
      store.read(receipt.id, { ...scope, user_id: "other" }, "session"),
    ).toThrow("not saved");
    expect(() => store.read(receipt.id, scope, "other")).toThrow();
    store.forget(receipt.id, { ...scope, workspace_id: "other" }, "session");
    expect(store.read(receipt.id, scope, "session").text).toBe(input.text);
    store.forget(receipt.id, scope, "session");
    expect(() => store.read(receipt.id, scope, "session")).toThrow();
  });
  it("expires at ten minutes without extending lifetime on replay", () => {
    let now = 1000;
    const store = new StudentVoiceReceiptStore(() => now);
    const receipt = store.put(input);
    now += STUDENT_VOICE_TTL_MS - 1;
    expect(store.read(receipt.id, scope, "session", true).expiresAt).toBe(
      1000 + STUDENT_VOICE_TTL_MS,
    );
    now += 1;
    expect(() => store.read(receipt.id, scope, "session")).toThrow();
    expect(store.size().count).toBe(0);
  });
  it("limits repeated playback and per-account retained answers", () => {
    const store = new StudentVoiceReceiptStore();
    const first = store.put(input);
    for (let index = 0; index < 3; index += 1)
      store.read(first.id, scope, "session", true);
    expect(() => store.read(first.id, scope, "session", true)).toThrow(
      "playback limit",
    );
    for (let index = 0; index < 8; index += 1) store.put(input);
    expect(store.size().count).toBe(8);
    expect(() => store.read(first.id, scope, "session")).toThrow();
  });
  it("caps total process memory and truthfully loses receipts on restart", () => {
    const store = new StudentVoiceReceiptStore();
    const first = store.put(input);
    for (let index = 0; index < 150; index += 1)
      store.put({ ...input, scope: { ...scope, user_id: `user-${index}` } });
    expect(store.size().count).toBeLessThanOrEqual(128);
    expect(store.size().bytes).toBeLessThanOrEqual(256 * 1024);
    expect(() => store.read(first.id, scope, "session")).toThrow();
    expect(() =>
      new StudentVoiceReceiptStore().read(first.id, scope, "session"),
    ).toThrow("not saved");
  });
});
