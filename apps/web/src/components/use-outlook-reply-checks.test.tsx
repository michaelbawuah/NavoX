// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { type OutlookDraft, readOutlookDraft } from "../lib/outlook";
import {
  mergeOutlookReplyReads,
  quietReplyCandidates,
  useOutlookReplyChecks,
} from "./use-outlook-reply-checks";

const connection = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "owner@example.com",
  status: "CONNECTED",
  read_enabled: true,
  send_enabled: false,
};
function receipt(index: number, changes: Partial<OutlookDraft> = {}) {
  return readOutlookDraft({
    id: `22222222-2222-4222-8222-${String(index).padStart(12, "0")}`,
    revision: 1,
    payload_hash: "a".repeat(64),
    connection_id: connection.id,
    source_message_id: `source-${index}`,
    sender: connection.email,
    to: "teacher@example.com",
    subject: "Reply",
    body: "Thank you",
    status: "completed",
    expires_at: null,
    verified_at: "2026-10-24T10:01:00Z",
    sent_at: "2026-10-24T10:00:00Z",
    reply_state: "waiting",
    reply_checked_at: "2026-10-24T10:02:00Z",
    reply_scan_count: 0,
    reply_scan_complete: true,
    reply_tracking_ready: true,
    content_available: true,
    ...changes,
  });
}
const saved = [receipt(1), receipt(2), receipt(3)];
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.useFakeTimers();
  vi.setSystemTime("2026-10-24T10:10:00Z");
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
function Harness({
  accountKey = "workspace:owner",
  editorOpen = false,
  onChecked,
}: {
  accountKey?: string;
  editorOpen?: boolean;
  onChecked: (draft: OutlookDraft) => void;
}) {
  const incomplete = useOutlookReplyChecks({
    accountKey,
    connection,
    drafts: saved,
    editorOpen,
    onChecked,
  });
  return <p>{incomplete ? "Some checks incomplete" : "Reply list"}</p>;
}
function availableFetch() {
  const fetcher = vi.fn(async (url: unknown, init?: RequestInit) => {
    const value = saved.find((draft) => String(url).includes(draft.id));
    expect(value).toBeDefined();
    expect(String(url)).toMatch(/\/check-replies$/);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ automatic: true });
    return Response.json({
      ...value,
      reply_checked_at: new Date().toISOString(),
    });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

it("checks at most two eligible receipts per minute and rotates toward older checks", async () => {
  const fetcher = availableFetch(),
    changed = vi.fn();
  await act(async () => root.render(<Harness onChecked={changed} />));
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(changed).toHaveBeenCalledTimes(2);
  await act(async () => vi.advanceTimersByTimeAsync(59_999));
  expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(fetcher).toHaveBeenCalledTimes(4);
  expect(fetcher.mock.calls[2][0]).toContain(saved[2].id);
  expect(
    fetcher.mock.calls.every(
      ([, init]) =>
        new Headers(init?.headers).get("X-NavoX-Account") === "workspace:owner",
    ),
  ).toBe(true);
});

it("stays idle with an open editor and resumes at the next visible cadence", async () => {
  const fetcher = availableFetch(),
    changed = vi.fn();
  await act(async () =>
    root.render(<Harness editorOpen onChecked={changed} />),
  );
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(fetcher).not.toHaveBeenCalled();
  await act(async () =>
    root.render(<Harness editorOpen={false} onChecked={changed} />),
  );
  expect(fetcher).not.toHaveBeenCalled();
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(fetcher).toHaveBeenCalledTimes(2);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => vi.advanceTimersByTimeAsync(120_000));
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it.each(["editor", "owner", "hidden"])(
  "aborts a delayed check on %s changes without publishing or overlapping",
  async (change) => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const changed = vi.fn();
    await act(async () => root.render(<Harness onChecked={changed} />));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(fetcher).toHaveBeenCalledTimes(1);
    if (change === "editor")
      await act(async () =>
        root.render(<Harness editorOpen onChecked={changed} />),
      );
    else if (change === "owner")
      await act(async () =>
        root.render(
          <Harness accountKey="workspace:new" editorOpen onChecked={changed} />,
        ),
      );
    else {
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      await act(async () =>
        document.dispatchEvent(new Event("visibilitychange")),
      );
    }
    await act(async () => finish(Response.json(saved[0])));
    expect(changed).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);

it("never auto-checks uncertain, privacy-cleared, legacy or mismatched-mailbox records", () => {
  const records = [
    { ...saved[0], status: "uncertain" as const },
    { ...saved[0], content_available: false },
    { ...saved[0], reply_tracking_ready: false },
    { ...saved[0], sent_at: null },
    { ...saved[0], connection_id: "33333333-3333-4333-8333-333333333333" },
    { ...saved[0], reply_state: "replied" as const },
  ];
  expect(quietReplyCandidates(connection, records)).toEqual([]);
  expect(
    quietReplyCandidates({ ...connection, read_enabled: false }, saved),
  ).toEqual([]);
  expect(
    quietReplyCandidates({ ...connection, status: "DISCONNECTED" }, saved),
  ).toEqual([]);
});

it("preserves newer reply proof against a delayed list while honoring cleared-content records", () => {
  const newer = receipt(1, {
    reply_state: "replied",
    reply_checked_at: "2026-10-24T10:09:00Z",
    reply_received_at: "2026-10-24T10:08:00Z",
  });
  expect(mergeOutlookReplyReads([saved[0]], [newer])[0].reply_state).toBe(
    "replied",
  );
  const cleared = {
    ...saved[0],
    content_available: false,
    body: "",
    reply_state: "not_checked" as const,
    reply_tracking_ready: false,
    reply_received_at: null,
  };
  expect(mergeOutlookReplyReads([cleared], [newer])[0]).toEqual(cleared);
});
