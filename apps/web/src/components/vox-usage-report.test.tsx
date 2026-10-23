// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountAccess } from "../lib/account-access";
import type { AccountSessionCallbacks } from "../lib/account-session";
import type { UsageReportStudent } from "../lib/vox-usage-report";

const observer = vi.hoisted(() => ({
  callbacks: null as AccountSessionCallbacks | null,
  dispose: vi.fn(),
}));
vi.mock("../lib/account-session", () => ({
  observeAccountSession: (callbacks: AccountSessionCallbacks) => {
    observer.callbacks = callbacks;
    return { dispose: observer.dispose, invalidate: vi.fn() };
  },
}));

import { VoxUsageReport, VoxUsageReportTable } from "./vox-usage-report";

const owner =
  "22222222-2222-4222-8222-222222222222:11111111-1111-4111-8111-111111111111";
const access: AccountAccess = {
  account: {
    id: owner.split(":")[1],
    email: "private-student@example.test",
    display_name: "Private name",
    workspace: {
      id: owner.split(":")[0],
      name: "Personal",
      workspace_type: "personal",
    },
  },
  connections: [],
  connectionsError: "",
};
const student: UsageReportStudent = {
  userId: "33333333-3333-4333-8333-333333333333",
  timezone: "Pacific/Kiritimati",
  localDay: "2026-10-20",
  speakingMs: 60_000,
  idleMs: 120_000,
  connectedMs: 180_000,
  tokens: 1234,
  reservedTokens: 22,
  estimatedCost: "0.0200",
  knownCost: "0.0200",
  unknownComponents: 0,
  costBasis: "estimate",
  reconciledCost: null,
};
function response() {
  return Response.json({
    students: [
      {
        user_id: student.userId,
        timezone: student.timezone,
        local_day: student.localDay,
        totals: {
          speaking_ms: student.speakingMs,
          idle_ms: student.idleMs,
          connected_ms: student.connectedMs,
          tokens: student.tokens,
          reserved_tokens: student.reservedTokens,
          estimated_cost_usd: student.estimatedCost,
          known_estimated_cost_usd: student.knownCost,
          unknown_cost_components: student.unknownComponents,
          reconciled_cost_usd: null,
          cost_basis: student.costBasis,
          transport_billing_notice: "Connected time includes idle.",
        },
      },
    ],
    next_after_user_id: null,
  });
}
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => response()),
  );
  observer.dispose.mockClear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  observer.callbacks = null;
  vi.unstubAllGlobals();
});
async function mount() {
  await act(async () => {
    root.render(createElement(VoxUsageReport, { accountId: owner }));
  });
}
async function restore() {
  await act(async () => {
    observer.callbacks?.restored(access);
  });
}

describe("read-only operator report in Settings", () => {
  it("renders nothing before operator permission is established, with no server-render fetch", () => {
    const html = renderToStaticMarkup(
      createElement(VoxUsageReport, { accountId: owner }),
    );
    expect(html).toBe("");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("formats exact money once and keeps unknown costs distinct from their known portion", () => {
    const unknown = {
      ...student,
      userId: "44444444-4444-4444-8444-444444444444",
      estimatedCost: null,
      knownCost: "0.00000000",
      unknownComponents: 2,
      costBasis: "unknown" as const,
    };
    const html = renderToStaticMarkup(
      createElement(VoxUsageReportTable, { rows: [student, unknown] }),
    );
    expect(html).toContain("Daily totals · minutes and USD");
    expect(html).not.toContain("Server-recorded");
    expect(html).toContain("$0.0200 USD");
    expect(html).toContain("Unknown");
    expect(html).toContain("Known portion: $0.00000000 USD");
    expect(html).not.toContain("$$");
    expect(html).toContain("Pacific/Kiritimati");
    expect(html).toContain("2026-10-20");
    expect(html).toContain("reserved, not yet used");
    expect(html).not.toContain(access.account?.email);
    expect(html).not.toContain("Private name");
  });
  it("labels reconciled amounts only when the server supplies one", () => {
    const html = renderToStaticMarkup(
      createElement(VoxUsageReportTable, {
        rows: [
          { ...student, reconciledCost: "0.0300", costBasis: "reconciled" },
        ],
      }),
    );
    expect(html).toContain("$0.0300 USD");
    expect(html).toContain("Reconciled");
    expect(html).not.toContain("$$");
  });
  it("shows only server-recorded numbers and removes rows on session checking or revoking", async () => {
    await mount();
    await restore();
    expect(host.textContent).toContain("Daily Vox usage");
    expect(host.textContent).toContain("Daily totals by student.");
    expect(host.textContent).not.toContain("Dates come from the server");
    expect(host.textContent).toContain("today in each student");
    expect(host.textContent).toContain("2026-10-20");
    expect(host.textContent).toContain(student.userId);
    expect(host.textContent).not.toContain("private-student@example.test");
    await act(async () => {
      observer.callbacks?.checking();
    });
    expect(host.innerHTML).toBe("");
    await restore();
    expect(host.textContent).toContain(student.userId);
    await act(async () => {
      observer.callbacks?.revoking?.();
    });
    expect(host.innerHTML).toBe("");
  });
  it("keeps403 invisible to students and rejects a mismatched restored owner", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => new Response(null, { status: 403 })),
    );
    await mount();
    await restore();
    expect(host.innerHTML).toBe("");
    expect(host.textContent).not.toContain("could not");
    const initial = access.account;
    if (!initial) throw new Error("Synthetic account missing");
    await act(async () => {
      observer.callbacks?.restored({
        ...access,
        account: {
          ...initial,
          id: "11111111-1111-4111-8111-111111111112",
        },
      });
    });
    expect(host.innerHTML).toBe("");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([401, 403])(
    "removes visible rows when fresh observer restoration receives %s",
    async (status) => {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(response())
        .mockResolvedValueOnce(new Response(null, { status }));
      vi.stubGlobal("fetch", fetcher);
      await mount();
      await restore();
      expect(host.textContent).toContain(student.userId);
      await restore();
      expect(host.innerHTML).toBe("");
      expect(host.textContent).not.toContain("could not");
    },
  );
  it("remounts a switched account without reusing the previous operator's data", async () => {
    await mount();
    await restore();
    expect(host.textContent).toContain(student.userId);
    const initial = access.account;
    if (!initial) throw new Error("Synthetic account missing");
    const switched = { ...initial, id: "11111111-1111-4111-8111-111111111112" };
    const nextOwner = `${switched.workspace.id}:${switched.id}`;
    await act(async () => {
      root.render(
        createElement(VoxUsageReport, { key: nextOwner, accountId: nextOwner }),
      );
    });
    expect(host.innerHTML).toBe("");
    expect(observer.dispose).toHaveBeenCalled();
    await act(async () => {
      observer.callbacks?.restored({ ...access, account: switched });
    });
    expect(host.textContent).toContain(student.userId);
    expect(vi.mocked(fetch).mock.calls.at(-1)?.[1]?.headers).toEqual({
      "X-NavoX-Account": nextOwner,
    });
  });
});
