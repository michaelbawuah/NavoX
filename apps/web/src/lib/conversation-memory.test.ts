import { afterEach, describe, expect, it, vi } from "vitest";
import {
  conversationMemoryRequest,
  MemoryClientError,
  readMemoryConversation,
  readMemoryFacts,
  readMemoryHistory,
  readMemoryPrivacy,
} from "./conversation-memory";

const privacy = {
  history_enabled: true,
  retention_days: 30,
  off_the_record: false,
  generation: 4,
  disclosure_version: 1,
};
const history = {
  id: "call-1",
  title: "Study plan",
  source_kind: "vox",
  created_at: "2026-10-26T12:00:00Z",
  updated_at: "2026-10-26T12:01:00Z",
  message_count: 2,
};
afterEach(() => vi.unstubAllGlobals());
describe("private memory contracts", () => {
  it("requires the disclosed history choice and a safe generation", () => {
    expect(readMemoryPrivacy(privacy)).toEqual(privacy);
    expect(() =>
      readMemoryPrivacy({ ...privacy, disclosure_version: null }),
    ).toThrow();
    expect(() =>
      readMemoryPrivacy({ ...privacy, retention_days: 60 }),
    ).toThrow();
    expect(() => readMemoryPrivacy({ ...privacy, generation: -1 })).toThrow();
    expect(
      readMemoryPrivacy({ ...privacy, retention_days: null }),
    ).toMatchObject({ retention_days: null });
  });
  it("rejects incomplete provenance and malformed history without displaying content", () => {
    expect(() =>
      readMemoryFacts([{ id: "fact", label: "Hello", content: "Secret" }]),
    ).toThrow();
    expect(() =>
      readMemoryHistory([{ ...history, message_count: -1 }]),
    ).toThrow();
    expect(() =>
      readMemoryConversation({
        ...history,
        summary: null,
        turns: [
          { role: "system", content: "Secret", created_at: history.created_at },
        ],
      }),
    ).toThrow();
  });
  it("binds requests to the displayed account and bypasses caches", async () => {
    const fetch = vi.fn(async () => Response.json(privacy));
    vi.stubGlobal("fetch", fetch);
    await conversationMemoryRequest(
      "school:student",
      "/privacy",
      readMemoryPrivacy,
      "PUT",
      { generation: 4 },
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringMatching(/\/memory\/privacy$/),
      expect.objectContaining({
        method: "PUT",
        credentials: "include",
        cache: "no-store",
        headers: {
          "X-NavoX-Account": "school:student",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ generation: 4 }),
      }),
    );
  });
  it("marks lost mutation responses uncertain and never exposes server detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "private provider text" }, { status: 503 }),
      ),
    );
    try {
      await conversationMemoryRequest(
        "school:student",
        "/facts",
        () => undefined,
        "POST",
        {},
      );
      throw new Error("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(MemoryClientError);
      expect((error as MemoryClientError).uncertain).toBe(true);
      expect(String(error)).not.toContain("private provider text");
    }
  });
});
