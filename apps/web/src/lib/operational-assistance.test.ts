import { afterEach, describe, expect, it, vi } from "vitest";
import { requestAssistance } from "./operational-assistance";

const api = "https://navox.example/api/v1";
const request = {
  domain: "assistant" as const,
  itemIds: ["task-a"],
  instructions: "Show the saved status.",
  sessionId: null,
};
const answer = { actions_executed: false, details: ["Task: waiting"] };
const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

afterEach(() => vi.unstubAllGlobals());

describe("saved-task assistance", () => {
  it("retains the NavoX session across turns without provider choices", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response({ id: "session-a" }))
      .mockResolvedValueOnce(response(answer))
      .mockResolvedValueOnce(response(answer));
    vi.stubGlobal("fetch", fetcher);
    const session = vi.fn();
    expect(await requestAssistance(api, request, session)).toEqual(answer);
    await requestAssistance(
      api,
      { ...request, sessionId: "session-a" },
      session,
    );
    expect(session).toHaveBeenCalledExactlyOnceWith("session-a");
    expect(fetcher).toHaveBeenCalledTimes(3);
    for (const call of fetcher.mock.calls.slice(1)) {
      expect(call[0]).toBe(`${api}/ai/operations/assistant`);
      expect(call[1].credentials).toBe("include");
      expect(JSON.parse(call[1].body)).toEqual({
        item_ids: ["task-a"],
        instructions: request.instructions,
        target_id: null,
        session_id: "session-a",
      });
    }
  });
  it("prepares a selected task without creating a conversation or action", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(answer));
    vi.stubGlobal("fetch", fetcher);
    await requestAssistance(api, { ...request, domain: "planning" }, vi.fn());
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`${api}/ai/operations/planning`);
    expect(JSON.parse(fetcher.mock.calls[0][1].body).target_id).toBe("task-a");
  });
  it.each([
    { itemIds: [] },
    { itemIds: ["a", "a"] },
    { itemIds: Array.from({ length: 13 }, (_, i) => String(i)) },
  ])("blocks invalid selections before any request", async ({ itemIds }) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await expect(
      requestAssistance(api, { ...request, itemIds }, vi.fn()),
    ).rejects.toThrow("distinct");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([403, 409, 503])(
    "reports unavailable results without retrying (%i)",
    async (status) => {
      const fetcher = vi.fn().mockResolvedValue(response({}, status));
      vi.stubGlobal("fetch", fetcher);
      await expect(
        requestAssistance(api, { ...request, sessionId: "session-a" }, vi.fn()),
      ).rejects.toThrow();
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );
  it("does not present an invalid response as an answer", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(response({ actions_executed: true, details: [] })),
    );
    await expect(
      requestAssistance(api, { ...request, domain: "ranking" }, vi.fn()),
    ).rejects.toThrow("could not be loaded");
  });
});
