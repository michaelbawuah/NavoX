import { describe, expect, it, vi } from "vitest";
import { createPurgeScheduler } from "./purge";

describe("bounded retention purge scheduler", () => {
  it("runs one pass with the clock time and returns the count", async () => {
    const seen: string[] = [];
    const scheduler = createPurgeScheduler({
      purge: async (now) => {
        seen.push(now);
        return 3;
      },
      now: () => new Date("2026-09-30T12:00:00.000Z"),
    });
    await expect(scheduler.runOnce()).resolves.toBe(3);
    expect(seen).toEqual(["2026-09-30T12:00:00.000Z"]);
  });

  it("never overlaps two passes", async () => {
    let calls = 0;
    let release!: (value: number) => void;
    const scheduler = createPurgeScheduler({
      purge: () => {
        calls += 1;
        return new Promise<number>((resolve) => {
          release = resolve;
        });
      },
    });
    const first = scheduler.runOnce();
    await expect(scheduler.runOnce()).resolves.toBe(0);
    expect(calls).toBe(1);
    release(4);
    await expect(first).resolves.toBe(4);
  });

  it("reports a purge failure without throwing", async () => {
    const errors: unknown[] = [];
    const scheduler = createPurgeScheduler({
      purge: async () => {
        throw new Error("database is unreachable");
      },
      onError: (error) => errors.push(error),
    });
    await expect(scheduler.runOnce()).resolves.toBe(0);
    expect(errors).toHaveLength(1);
  });

  it("starts exactly one unref'd timer and stops it once", async () => {
    const handlers: { handler: () => void; ms: number }[] = [];
    const cleared: unknown[] = [];
    const handle = { unref: vi.fn() };
    const purge = vi.fn(async () => 1);
    const scheduler = createPurgeScheduler({
      purge,
      intervalMs: 1000,
      setIntervalImpl: (handler, ms) => {
        handlers.push({ handler, ms });
        return handle;
      },
      clearIntervalImpl: (value) => cleared.push(value),
    });

    scheduler.start();
    scheduler.start();
    expect(handlers).toHaveLength(1);
    expect(handlers[0]?.ms).toBe(1000);
    expect(handle.unref).toHaveBeenCalledTimes(1);

    handlers[0]?.handler();
    expect(purge).toHaveBeenCalledTimes(1);

    scheduler.stop();
    expect(cleared).toEqual([handle]);
    scheduler.stop();
    expect(cleared).toHaveLength(1);
  });
});
