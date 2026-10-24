import { afterEach, expect, it, vi } from "vitest";
import {
  loadOutlookReplySummary,
  outlookReplyHref,
  readOutlookReplyTarget,
} from "./outlook-summary";

const account = "workspace:student";
const mailbox = (n = 1, status = "CONNECTED", read_enabled = true) => ({
  id: `0000000${n}-1111-4111-8111-111111111111`,
  email: `student${n}@example.test`,
  status,
  read_enabled,
  send_enabled: false,
});
const message = {
  id: "opaque/+id=",
  sender: "professor@example.test",
  subject: "Office hours",
  preview: "Please choose a time",
  reason: "Asked for a reply",
  sent_at: "2026-10-24T10:00:00Z",
  open_url: null,
};
const status = (
  connections: ReturnType<typeof mailbox>[],
  configured = true,
) => ({ configured, connections, notice: "" });
afterEach(() => vi.unstubAllGlobals());

it.each([
  ["CONNECTED", false, true],
  ["DEGRADED", false, true],
  ["REVOKED", true, true],
  ["PENDING", true, true],
  ["active", true, true],
  ["CONNECTED", true, false],
])(
  "does not count unavailable read consent: status=%s read=%s configured=%s",
  async (state, read, configured) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          status(
            [mailbox(1, String(state), Boolean(read))],
            Boolean(configured),
          ),
        ),
      ),
    );
    expect((await loadOutlookReplySummary(account)).connected).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
  },
);

it.each(["CONNECTED", "DEGRADED"])(
  "accepts actual %s read consent without requiring send permission",
  async (state) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) =>
        String(url).endsWith("/status")
          ? Response.json(status([mailbox(1, state)]))
          : Response.json({ messages: [message], has_more: false, notice: "" }),
      ),
    );
    const result = await loadOutlookReplySummary(account);
    expect(result.connected).toBe(true);
    expect(result.messages).toHaveLength(1);
  },
);

it("bounds mailbox scans, deduplicates sources, and counts only returned candidates with owner proof", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, _init?: RequestInit) =>
      String(url).endsWith("/status")
        ? Response.json(
            status([mailbox(1), mailbox(2), mailbox(3), mailbox(4)]),
          )
        : Response.json({
            messages: [message, message],
            has_more: true,
            notice: "More mail exists",
          }),
    ),
  );
  const result = await loadOutlookReplySummary(account);
  expect(result).toMatchObject({
    connected: true,
    checked_mailboxes: 3,
    available_mailboxes: 4,
    limited: true,
    unavailable: false,
  });
  expect(result.messages).toHaveLength(3);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).endsWith("/messages")),
  ).toHaveLength(3);
  for (const [url, init] of vi.mocked(fetch).mock.calls) {
    expect(String(url)).not.toContain("/drafts");
    expect(init).toMatchObject({
      method: "GET",
      credentials: "include",
      headers: { "X-NavoX-Account": account },
    });
  }
});

it("retains only known candidates when one readable mailbox fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const path = String(url);
      if (path.endsWith("/status"))
        return Response.json(status([mailbox(1), mailbox(2)]));
      if (path.includes(mailbox(2).id))
        return Response.json({}, { status: 503 });
      return Response.json({
        messages: [message],
        has_more: false,
        notice: "",
      });
    }),
  );
  const result = await loadOutlookReplySummary(account);
  expect(result).toMatchObject({
    connected: true,
    checked_mailboxes: 1,
    available_mailboxes: 2,
    unavailable: true,
  });
  expect(result.messages).toHaveLength(1);
});

it("removes a mailbox's candidates if its read permission is revoked during the scan", async () => {
  let checks = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      if (String(url).endsWith("/status"))
        return Response.json(
          status([++checks === 1 ? mailbox() : mailbox(1, "REVOKED", false)]),
        );
      return Response.json({
        messages: [message],
        has_more: false,
        notice: "",
      });
    }),
  );
  expect(await loadOutlookReplySummary(account)).toMatchObject({
    connected: false,
    messages: [],
    checked_mailboxes: 0,
  });
});

it("does not start mailbox reads after its captured owner is cancelled", async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const controller = new AbortController();
  const pending = loadOutlookReplySummary(account, controller.signal);
  controller.abort();
  finish(Response.json(status([mailbox()])));
  await expect(pending).rejects.toThrow("cancelled");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("keeps encoded Inbox navigation scoped to its owner and exact opaque source", () => {
  const url = new URL(
    outlookReplyHref(account, mailbox().id, message.id),
    "https://navox.example",
  );
  expect(readOutlookReplyTarget(url.search, account)).toEqual({
    connectionId: mailbox().id,
    messageId: message.id,
  });
  expect(readOutlookReplyTarget(url.search, "workspace:other")).toBeNull();
  expect(
    readOutlookReplyTarget(`${url.search}&outlook_reply=another`, account),
  ).toBeNull();
  url.searchParams.set("outlook_reply", "bad\nsource");
  expect(readOutlookReplyTarget(url.search, account)).toBeNull();
});
