// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
it("keeps an uncertain source in Waiting without offering a second send or a new draft", async () => {
  mockMailbox([draft]);
  await mount();
  expect(host.textContent).toContain("Needs reply · 0");
  expect(host.textContent).not.toContain(message.preview);
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
  expect(host.textContent).toContain("Waiting · 1");
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
