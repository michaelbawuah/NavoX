import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type RecheckItem,
  recheckRequest,
  removalSelection,
  runRecheckBatch,
} from "./gmail-recheck";

function item(id: string, outcome = "unchecked"): RecheckItem {
  return {
    commitment_id: id,
    title: "Saved item",
    outcome,
    reason: "not_checked",
    preview_id: outcome === "unchecked" ? null : `preview-${id}`,
    checked_at: null,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("Gmail cleanup requests", () => {
  it("requires selected, completed removal suggestions and limits the selection", () => {
    const rows = [
      item("a", "remove_suggested"),
      item("b", "retained"),
      item("c", "failed"),
      item("d"),
    ];
    expect(removalSelection(rows, new Set())).toEqual([]);
    expect(
      removalSelection(rows, new Set(["a", "b", "c", "d", "foreign"])),
    ).toEqual([{ commitment_id: "a", preview_id: "preview-a" }]);
    const many = Array.from({ length: 30 }, (_, i) =>
      item(`${i}`, "remove_suggested"),
    );
    expect(
      removalSelection(many, new Set(many.map((row) => row.commitment_id))),
    ).toHaveLength(25);
  });

  it("checks at most ten unchecked items sequentially and never applies changes", async () => {
    let active = 0;
    const preview = vi.fn(async (row: RecheckItem) => {
      active += 1;
      expect(active).toBe(1);
      await Promise.resolve();
      active -= 1;
      return { ...row, outcome: "remove_suggested" };
    });
    const onResult = vi.fn();
    await runRecheckBatch({
      items: [
        item("saved", "retained"),
        ...Array.from({ length: 12 }, (_, i) => item(`${i}`)),
      ],
      preview,
      onStart: vi.fn(),
      onResult,
      shouldStop: () => false,
    });
    expect(preview).toHaveBeenCalledTimes(10);
    expect(onResult).toHaveBeenCalledTimes(10);
    expect(preview.mock.calls[0][0].commitment_id).toBe("0");
  });

  it.each(["failed", "checking"])(
    "stops the batch on %s without retries",
    async (outcome) => {
      const preview = vi.fn(async (row: RecheckItem) => ({ ...row, outcome }));
      await runRecheckBatch({
        items: [item("a"), item("b")],
        preview,
        onStart: vi.fn(),
        onResult: vi.fn(),
        shouldStop: () => false,
      });
      expect(preview).toHaveBeenCalledTimes(1);
    },
  );

  it("honors stop or pause after the current check, preserving its result", async () => {
    let stopped = false;
    const preview = vi.fn(async (row: RecheckItem) => {
      stopped = true;
      return { ...row, outcome: "retained" };
    });
    const onResult = vi.fn();
    await runRecheckBatch({
      items: [item("a"), item("b")],
      preview,
      onStart: vi.fn(),
      onResult,
      shouldStop: () => stopped,
    });
    expect(preview).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith(
      expect.objectContaining({ commitment_id: "a", outcome: "retained" }),
    );
  });

  it("uses authenticated uncached requests with only selected preview identifiers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ applied: ["a"], skipped: [] })),
      );
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;
    const body = {
      connection_id: "connection",
      action: "remove",
      items: [{ commitment_id: "a", preview_id: "preview-a" }],
    };
    await recheckRequest("/apply", signal, body);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/intelligence/gmail-recheck/apply"),
      {
        credentials: "include",
        cache: "no-store",
        signal,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  });
});
