// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { outlookReplyHref } from "../lib/outlook-summary";
import { InboxWelcome } from "./inbox-welcome";
import { OutlookInbox } from "./outlook-inbox";
import { outlookSendKey } from "./outlook-reply-editor";

vi.mock("../lib/product-features", () => ({
  productFeatures: { studentCalendarAvailability: false },
}));
const id = "11111111-1111-4111-8111-111111111111";
const connection = {
  id,
  email: "student@example.com",
  status: "CONNECTED",
  read_enabled: true,
  send_enabled: true,
};
const message = {
  id: "opaque/+id=",
  sender: "professor@example.com",
  subject: "Office hours",
  preview: "Please choose a time",
  sent_at: "2026-10-24T10:00:00Z",
  open_url: "https://outlook.office.com/mail/id/opaque",
  reason: "Asked for a reply",
};
const draft = {
  id,
  revision: 2,
  payload_hash: "a".repeat(64),
  connection_id: id,
  source_message_id: message.id,
  sender: connection.email,
  to: message.sender,
  subject: "Re: Office hours",
  body: "Could we meet on Tuesday?",
  status: "uncertain",
  expires_at: "2099-01-01T10:00:00Z",
  verified_at: null,
  notice: "Send outcome not confirmed",
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  sessionStorage.clear();
  window.history.replaceState({}, "", "/inbox");
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function mount(accountKey = "workspace:student") {
  await act(async () =>
    root.render(
      <OutlookInbox
        accountKey={accountKey}
        timezone="UTC"
        fallback={<InboxWelcome />}
      />,
    ),
  );
}
function mockMailbox(drafts: unknown[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, _init?: RequestInit) => {
      const path = String(url);
      if (path.endsWith("/status"))
        return Response.json({
          configured: true,
          connections: [connection],
          notice: "",
        });
      if (path.endsWith("/drafts")) return Response.json(drafts);
      if (path.endsWith("/messages"))
        return Response.json({
          messages: [message],
          has_more: false,
          notice: "Checked Outlook messages",
        });
      if (path.endsWith(`/messages/${encodeURIComponent(message.id)}`))
        return Response.json({
          ...message,
          to: connection.email,
          body: "Please choose a time to meet.",
        });
      throw new Error(`Unexpected request: ${path}`);
    }),
  );
}
function chooseMessage() {
  const row = [...host.querySelectorAll("button")].find((button) =>
    button.textContent?.includes(message.preview),
  );
  expect(row).toBeDefined();
  row!.click();
}
it("offers setup examples only after the Outlook status is known", async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  await mount();
  expect(host.textContent).toContain("Checking your email connections");
  expect(host.textContent).not.toContain("Connect Gmail");
  await act(async () =>
    finish(
      Response.json({
        configured: false,
        connections: [],
        notice: "Setup pending",
      }),
    ),
  );
  expect(host.textContent).toContain("Never miss an email that needs you");
  expect(
    host.querySelector('[aria-label="Example emails, not your inbox"]'),
  ).not.toBeNull();
  expect(
    host.querySelector('a[href="/connections?source=outlook#outlook"]')
      ?.textContent,
  ).toContain("Connect Outlook");
  expect(host.querySelector("textarea")).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("keeps unavailable status distinct from a disconnected mailbox", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("offline");
    }),
  );
  await mount();
  expect(host.textContent).toContain("Outlook status couldn’t load");
  expect(host.textContent).not.toContain("Never miss an email that needs you");
  expect(host.querySelector("textarea")).toBeNull();
});
it("keeps an uncertain source separate from Waiting without offering a second send or a new draft", async () => {
  mockMailbox([draft]);
  await mount();
  expect(host.textContent).toContain("Needs reply · 0");
  expect(host.textContent).not.toContain(message.preview);
  expect(host.textContent).toContain("Waiting · 0");
  expect(host.textContent).toContain("Unconfirmed sends · 1");
  await act(async () =>
    [...host.querySelectorAll("button")]
      .find((button) => button.textContent?.includes(draft.subject))!
      .click(),
  );
  expect(host.textContent).toContain("Check your reply’s outcome");
  expect(host.querySelector("textarea")?.value).toBe(draft.body);
  expect(host.textContent).not.toContain("Send this reply");
  expect(host.textContent).not.toContain("Draft reply");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([url]) =>
        String(url).endsWith(`/messages/${encodeURIComponent(message.id)}`),
      ),
  ).toBe(true);
  expect(
    vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === "GET"),
  ).toBe(true);
});
it("reopens the existing saved review when its source email is selected", async () => {
  mockMailbox([{ ...draft, status: "review" }]);
  await mount();
  await act(async () => chooseMessage());
  expect(host.querySelector("textarea")?.value).toBe(draft.body);
  expect(host.textContent).toContain("Send this reply");
  expect(
    vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === "GET"),
  ).toBe(true);
});
it("keeps locally unconfirmed sends out of Needs reply even before the server record changes", async () => {
  sessionStorage.setItem(
    outlookSendKey("workspace:student", id),
    "pending-request",
  );
  mockMailbox([{ ...draft, status: "review" }]);
  await mount();
  expect(host.textContent).toContain("Needs reply · 0");
  expect(host.textContent).toContain("Waiting · 0");
  expect(host.textContent).toContain("Unconfirmed sends · 1");
  expect(host.textContent).not.toContain(message.preview);
});
it("refreshes mailbox reads on focus without replacing an unfinished reply", async () => {
  mockMailbox();
  await mount();
  await act(async () => chooseMessage());
  const body = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!.call(body, "My unfinished reply");
    body.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(host.querySelector("textarea")?.value).toBe("My unfinished reply");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).endsWith("/messages")),
  ).toHaveLength(2);
  expect(
    vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === "GET"),
  ).toBe(true);
});
it("opens Today's exact Outlook source and reuses its pending receipt without generating or sending", async () => {
  window.history.replaceState(
    {},
    "",
    outlookReplyHref("workspace:student", id, message.id),
  );
  mockMailbox([draft]);
  await mount();
  expect(host.textContent).toContain("Please choose a time to meet.");
  expect(host.textContent).toContain("Check your reply’s outcome");
  expect(host.querySelector("textarea")?.value).toBe(draft.body);
  expect(host.textContent).not.toContain("Send this reply");
  expect(new URLSearchParams(window.location.search).has("outlook_reply")).toBe(
    false,
  );
  expect(
    vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === "GET"),
  ).toBe(true);
});
it("ignores a Today source hint captured for another owner", async () => {
  window.history.replaceState(
    {},
    "",
    outlookReplyHref("workspace:old", id, message.id),
  );
  mockMailbox();
  await mount("workspace:new");
  expect(host.querySelector("textarea")).toBeNull();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([url]) =>
        String(url).endsWith(`/messages/${encodeURIComponent(message.id)}`),
      ),
  ).toBe(false);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(
        ([, init]) =>
          new Headers(init?.headers).get("X-NavoX-Account") === "workspace:new",
      ),
  ).toBe(true);
});

it("counts only checked waiting replies and keeps unknown checks and replied receipts distinct", async () => {
  const sent = {
    ...draft,
    status: "completed",
    verified_at: "2026-10-24T10:01:00Z",
    sent_at: "2026-10-24T10:00:00Z",
  };
  mockMailbox([
    {
      ...sent,
      reply_state: "waiting",
      reply_checked_at: "2026-10-24T10:02:00Z",
      reply_scan_count: 0,
      reply_scan_complete: true,
    },
    {
      ...sent,
      id: "22222222-2222-4222-8222-222222222222",
      subject: "Unchecked reply",
      reply_state: "unknown",
      reply_scan_complete: false,
    },
    {
      ...sent,
      id: "33333333-3333-4333-8333-333333333333",
      subject: "A reply arrived",
      reply_state: "replied",
      reply_received_at: "2026-10-24T10:03:00Z",
    },
  ]);
  await mount();
  expect(host.textContent).toContain("Waiting · 1");
  expect(host.textContent).toContain("Sent replies · 1");
  expect(host.textContent).toContain("Reply status needs checking · 1");
  expect(host.textContent).not.toContain("Unconfirmed sends ·");
  await act(async () =>
    [...host.querySelectorAll("button")]
      .find((button) => button.textContent === "Sent replies · 1")!
      .click(),
  );
  expect(host.textContent).toContain("A reply arrived");
  expect(host.textContent).toContain("not your full Outlook Sent folder");
  expect(
    vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === "GET"),
  ).toBe(true);
});

it("retires a waiting reply only after the explicit owner-bound reply check returns evidence", async () => {
  let saved = {
    ...draft,
    status: "completed",
    verified_at: "2026-10-24T10:01:00Z",
    sent_at: "2026-10-24T10:00:00Z",
    reply_state: "waiting",
    reply_checked_at: "2026-10-24T10:02:00Z",
    reply_received_at: null as string | null,
    reply_scan_count: 0,
    reply_scan_complete: true,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: RequestInit) => {
      const path = String(url);
      if (path.endsWith("/status"))
        return Response.json({
          configured: true,
          connections: [connection],
          notice: "",
        });
      if (path.endsWith("/drafts")) return Response.json([saved]);
      if (path.endsWith("/messages"))
        return Response.json({ messages: [], has_more: false, notice: "" });
      if (path.includes("/messages/"))
        return Response.json({
          ...message,
          to: connection.email,
          body: "Please choose a time.",
        });
      if (path.endsWith(`/drafts/${id}/check-replies`)) {
        expect(init?.method).toBe("POST");
        expect(new Headers(init?.headers).get("X-NavoX-Account")).toBe(
          "workspace:student",
        );
        expect(JSON.parse(String(init?.body))).toEqual({});
        saved = {
          ...saved,
          reply_state: "replied",
          reply_scan_count: 1,
          reply_received_at: "2026-10-24T10:03:00Z",
        };
        return Response.json(saved);
      }
      throw new Error(`Unexpected request: ${path}`);
    }),
  );
  await mount();
  await act(async () =>
    [...host.querySelectorAll("button")]
      .find((button) => button.textContent === "Waiting · 1")!
      .click(),
  );
  await act(async () =>
    [...host.querySelectorAll("button")]
      .find((button) => button.textContent?.includes(draft.subject))!
      .click(),
  );
  expect(host.textContent).toContain("read up to 50 later messages");
  await act(async () =>
    [...host.querySelectorAll("button")]
      .find((button) => button.textContent === "Check for replies")!
      .click(),
  );
  expect(host.textContent).toContain("Waiting · 0");
  expect(host.textContent).toContain("Sent replies · 1");
  expect(host.textContent).toContain("Reply found");
  expect(
    vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/send")),
  ).toBe(false);
});
