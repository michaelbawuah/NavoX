import { afterEach, describe, expect, it, vi } from "vitest";
import { readSourceEvidence } from "./source-evidence";

afterEach(() => vi.unstubAllGlobals());

describe("source evidence reads", () => {
  it("uses an explicit authenticated uncached request and accepts exact Unicode quotes", async () => {
    const excerpt = { source: "content", text: "🙂".repeat(512) };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ evidence_id: "evidence-1", excerpts: [excerpt] }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;
    expect(await readSourceEvidence("/api/v1", "evidence-1", signal)).toEqual([
      excerpt,
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/intelligence/evidence/evidence-1",
      {
        credentials: "include",
        cache: "no-store",
        signal,
      },
    );
  });

  it.each([403, 409, 410, 429, 502, 504])(
    "keeps provider prose out of error UI for %s",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            new Response("PRIVATE PROVIDER ERROR", { status }),
          ),
      );
      const result = readSourceEvidence(
        "/api/v1",
        "evidence-1",
        new AbortController().signal,
      );
      await expect(result).rejects.not.toThrow("PRIVATE");
      await expect(result).rejects.toThrow();
    },
  );

  it.each([
    {
      evidence_id: "wrong",
      excerpts: [{ source: "content", text: "private" }],
    },
    {
      evidence_id: "evidence-1",
      excerpts: [{ source: "content", text: "x".repeat(513) }],
    },
    {
      evidence_id: "evidence-1",
      excerpts: [{ source: "html", text: "private" }],
    },
    { evidence_id: "evidence-1", excerpts: [] },
  ])("rejects mismatched or unbounded replies", async (data) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(data))),
    );
    await expect(
      readSourceEvidence("/api/v1", "evidence-1", new AbortController().signal),
    ).rejects.toThrow("could not be verified");
  });
});
