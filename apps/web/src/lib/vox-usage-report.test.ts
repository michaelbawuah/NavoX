import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  readUsageReportPage,
  restoreUsageReportAccess,
  type UsageReportState,
  VoxUsageReportController,
  validUsageReportDate,
} from "./vox-usage-report";

const owner =
  "22222222-2222-4222-8222-222222222222:11111111-1111-4111-8111-111111111111";
const other =
  "22222222-2222-4222-8222-222222222222:11111111-1111-4111-8111-111111111112";
const studentId = (number: number) =>
  `33333333-3333-4333-8333-${String(number).padStart(12, "0")}`;
function row(number = 1, day = "2026-10-19") {
  return {
    user_id: studentId(number),
    timezone: "America/New_York",
    local_day: day,
    totals: {
      speaking_ms: 20_000,
      idle_ms: 40_000,
      connected_ms: 60_000,
      tokens: 1234,
      reserved_tokens: 0,
      estimated_cost_usd: "0.0200" as string | null,
      known_estimated_cost_usd: "0.0200" as string | null,
      unknown_cost_components: 0,
      reconciled_cost_usd: null as string | null,
      cost_basis: "estimate",
      transport_billing_notice: "Connected room time includes idle.",
    },
  };
}
function page(count = 1, start = 1, day = "2026-10-19") {
  const students = Array.from({ length: count }, (_, index) =>
    row(start + index, day),
  );
  return {
    students,
    next_after_user_id:
      count === 25 ? (students.at(-1)?.user_id ?? null) : null,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function settle() {
  for (let index = 0; index < 25; index++) await Promise.resolve();
}
const reports: VoxUsageReportController[] = [];
function rig(fetcher = vi.fn<typeof fetch>(async () => Response.json(page()))) {
  let state: UsageReportState | null = null;
  const onChange = vi.fn((next: UsageReportState) => {
    state = next;
  });
  const report = new VoxUsageReportController({
    apiBaseUrl: "https://navox.example/api/v1",
    fetch: fetcher,
    onChange,
  });
  reports.push(report);
  return { report, fetcher, onChange, current: () => state };
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  for (const report of reports.splice(0)) report.dispose();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("operator daily usage reads", () => {
  it("uses only the private read endpoint and server-local days, never browser today", async () => {
    vi.setSystemTime(new Date("2050-01-01T12:00:00Z"));
    const env = rig();
    env.report.activate(owner);
    await settle();
    expect(env.fetcher).toHaveBeenCalledExactlyOnceWith(
      "https://navox.example/api/v1/vox/usage/daily?limit=25",
      {
        method: "GET",
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        headers: { "X-NavoX-Account": owner },
        signal: expect.any(AbortSignal),
      },
    );
    expect(env.current()?.day).toBeNull();
    expect(env.current()?.students[0].localDay).toBe("2026-10-19");
    expect(env.current()?.students[0].timezone).toBe("America/New_York");
  });
  it("does not show a report or an error card to students receiving403", async () => {
    const json = vi.fn();
    const env = rig(
      vi.fn<typeof fetch>(
        async () => ({ status: 403, ok: false, json }) as unknown as Response,
      ),
    );
    env.report.activate(owner);
    await settle();
    expect(env.current()?.phase).toBe("hidden");
    expect(env.current()?.students).toEqual([]);
    expect(json).not.toHaveBeenCalled();
  });
  it.each([401, 403, 409])(
    "clears previously visible totals on %s",
    async (status) => {
      const env = rig(
        vi
          .fn<typeof fetch>()
          .mockResolvedValueOnce(Response.json(page()))
          .mockResolvedValueOnce(new Response(null, { status })),
      );
      env.report.activate(owner);
      await settle();
      expect(env.current()?.students).toHaveLength(1);
      env.report.refresh(null);
      await settle();
      expect(env.current()?.phase).toBe("hidden");
      expect(env.current()?.students).toEqual([]);
    },
  );
  it("preserves unknown costs and keeps future voice reservations out of actual totals", () => {
    const value = row();
    value.totals.estimated_cost_usd = null;
    value.totals.cost_basis = "unknown";
    value.totals.known_estimated_cost_usd = "0E-10";
    value.totals.unknown_cost_components = 2;
    const parsed = readUsageReportPage({
      students: [
        {
          ...value,
          totals: {
            ...value.totals,
            reserved_voice_ms: 999_999,
            voice_credit_uncertain: true,
          },
        },
      ],
      next_after_user_id: null,
    });
    expect(parsed.students[0]).toMatchObject({
      estimatedCost: null,
      costBasis: "unknown",
      knownCost: "0.0000000000",
      speakingMs: 20_000,
      connectedMs: 60_000,
      tokens: 1234,
    });
    expect(parsed.students[0]).not.toHaveProperty("reserved_voice_ms");
  });
  it.each([
    "2026-02-30",
    "2026-02-29",
    "20261019",
    "2026-W43-1",
    "9999-12-31",
    "0001-01-01",
  ])(
    "rejects invalid or unsupported explicit date %s without a request",
    async (day) => {
      const env = rig();
      env.report.activate(owner);
      await settle();
      expect(validUsageReportDate(day)).toBe(false);
      expect(env.report.refresh(day)).toBe(false);
      expect(env.fetcher).toHaveBeenCalledTimes(1);
    },
  );
  it("validates actual leap dates and preserves captured explicit dates through pagination", async () => {
    expect(validUsageReportDate("2028-02-29")).toBe(true);
    const env = rig(
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(Response.json(page()))
        .mockResolvedValueOnce(Response.json(page(25, 1, "2028-02-29")))
        .mockResolvedValueOnce(Response.json(page(1, 26, "2028-02-29")))
        .mockResolvedValueOnce(Response.json(page(25, 1, "2028-02-29"))),
    );
    env.report.activate(owner);
    await settle();
    env.report.refresh("2028-02-29");
    await settle();
    env.report.next();
    env.report.next();
    await settle();
    expect(env.fetcher).toHaveBeenCalledTimes(3);
    const target = new URL(String(env.fetcher.mock.calls[2][0]));
    expect(target.searchParams.get("day")).toBe("2028-02-29");
    expect(target.searchParams.get("after_user_id")).toBe(studentId(25));
    expect(env.current()?.students).toHaveLength(1);
    expect(env.current()?.students[0].userId).toBe(studentId(26));
    env.report.first();
    await settle();
    expect(
      new URL(String(env.fetcher.mock.calls[3][0])).searchParams.has(
        "after_user_id",
      ),
    ).toBe(false);
  });
  it.each([owner, other])(
    "drops late JSON after fresh session activation for %s",
    async (replacement) => {
      const pending = deferred<unknown>();
      const env = rig(
        vi
          .fn<typeof fetch>()
          .mockResolvedValueOnce({
            ok: true,
            status: 200,
            json: () => pending.promise,
          } as Response)
          .mockResolvedValueOnce(Response.json(page(1, 2))),
      );
      env.report.activate(owner);
      await settle();
      const oldSignal = env.fetcher.mock.calls[0][1]?.signal;
      env.report.clear();
      expect(oldSignal?.aborted).toBe(true);
      env.report.activate(replacement);
      await settle();
      pending.resolve(page());
      await settle();
      expect(env.current()?.students[0].userId).toBe(studentId(2));
      expect(
        env.onChange.mock.calls.some(([state]) =>
          state.students.some((student) => student.userId === studentId(1)),
        ),
      ).toBe(false);
    },
  );
  it("drops a stale date-page reply after selecting another date", async () => {
    const pending = deferred<Response>();
    const env = rig(
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(Response.json(page(25)))
        .mockReturnValueOnce(pending.promise)
        .mockResolvedValueOnce(Response.json(page(1, 7, "2026-10-20"))),
    );
    env.report.activate(owner);
    await settle();
    env.report.next();
    env.report.refresh("2026-10-20");
    await settle();
    pending.resolve(Response.json(page(1, 26)));
    await settle();
    expect(env.current()?.day).toBe("2026-10-20");
    expect(env.current()?.students[0].userId).toBe(studentId(7));
  });
  it("times out a refresh without inventing zero totals and ignores transport that disregards abort", async () => {
    const pending = deferred<Response>();
    const env = rig(
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(Response.json(page()))
        .mockReturnValueOnce(pending.promise),
    );
    env.report.activate(owner);
    await settle();
    env.report.refresh(null);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(env.current()?.phase).toBe("error");
    expect(env.current()?.students).toEqual([]);
    pending.resolve(Response.json(page()));
    await settle();
    expect(env.current()?.phase).toBe("error");
  });
  it("fails closed on contradictory server counters, malformed prices, unwanted PII, or wrong dates", async () => {
    const malformed = [
      { ...row(), email: "must-not-appear@example.test" },
      { ...row(), totals: { ...row().totals, speaking_ms: -1 } },
      { ...row(), totals: { ...row().totals, connected_ms: 30_000 } },
      {
        ...row(),
        totals: { ...row().totals, tokens: Number.MAX_SAFE_INTEGER + 1 },
      },
      { ...row(), totals: { ...row().totals, estimated_cost_usd: "NaN" } },
      { ...row(), totals: { ...row().totals, cost_basis: "unknown" } },
    ];
    for (const item of malformed)
      expect(() =>
        readUsageReportPage({ students: [item], next_after_user_id: null }),
      ).toThrow();
    const env = rig(
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(Response.json(page()))
        .mockResolvedValueOnce(Response.json(page(1, 1, "2026-10-19"))),
    );
    env.report.activate(owner);
    await settle();
    env.report.refresh("2026-10-20");
    await settle();
    expect(env.current()?.phase).toBe("error");
    expect(env.current()?.students).toEqual([]);
  });
  it("restores only auth/me, without reading connected student records", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      Response.json({
        id: owner.split(":")[1],
        email: "synthetic@example.test",
        display_name: "Synthetic",
        workspace: {
          id: owner.split(":")[0],
          name: "Personal",
          workspace_type: "personal",
        },
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    const access = await restoreUsageReportAccess(
      "https://navox.example/api/v1",
    );
    expect(access.account?.id).toBe(owner.split(":")[1]);
    expect(access.connections).toEqual([]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://navox.example/api/v1/auth/me",
    );
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      credentials: "include",
      cache: "no-store",
      redirect: "error",
    });
  });
});
