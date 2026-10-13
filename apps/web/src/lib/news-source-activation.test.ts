import { afterEach, describe, expect, it, vi } from "vitest";
import { addNewsSource, NewsRequestError } from "./news";

afterEach(() => vi.unstubAllGlobals());

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("first news source refresh", () => {
  it("refreshes the newly returned source only after activation succeeds", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({ id: "source-one", status: "active" }, 201))
      .mockResolvedValueOnce(json({ status: "COMPLETED", stored_count: 3 }));
    vi.stubGlobal("fetch", fetch);

    expect(await addNewsSource("science-source")).toBe(
      "Source added and refreshed.",
    );
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(String(fetch.mock.calls[0]?.[0])).toMatch(
      /\/sources\/science-source\/activate$/,
    );
    expect(String(fetch.mock.calls[1]?.[0])).toMatch(
      /\/sources\/source-one\/refresh$/,
    );
    const commands = fetch.mock.calls.map((call) => JSON.parse(call[1].body));
    expect(commands[0].request_id).not.toBe(commands[1].request_id);
  });

  it.each([409, 503])(
    "keeps activation successful when refresh returns %s",
    async (status) => {
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(
          json({ id: "source-one", status: "active" }, 201),
        )
        .mockResolvedValueOnce(json({}, status));
      vi.stubGlobal("fetch", fetch);
      expect(await addNewsSource("science-source")).toBe(
        "Source added. Its first refresh didn’t finish. Use Refresh to try again.",
      );
      expect(fetch).toHaveBeenCalledTimes(2);
    },
  );

  it("does not announce fresh stories when ingestion failed", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({ id: "source-one", status: "active" }, 201))
      .mockResolvedValueOnce(json({ status: "FAILED" }));
    vi.stubGlobal("fetch", fetch);
    expect(await addNewsSource("science-source")).toContain(
      "first refresh didn’t finish",
    );
  });

  it("does not refresh when activation was rejected", async () => {
    const fetch = vi.fn().mockResolvedValue(json({}, 404));
    vi.stubGlobal("fetch", fetch);
    await expect(addNewsSource("private-source")).rejects.toBeInstanceOf(
      NewsRequestError,
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
