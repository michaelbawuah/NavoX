// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { OutlookDraft } from "../lib/outlook";
import { OutlookReplyEditor, outlookSendKey } from "./outlook-reply-editor";

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
const draft: OutlookDraft = {
  id,
  revision: 2,
  payload_hash: "a".repeat(64),
  connection_id: id,
  source_message_id: "opaque/id+",
  sender: connection.email,
  to: ["professor@example.com"],
  subject: "Office hours",
  body: "Could we meet on Tuesday?",
  status: "review",
  expires_at: "2099-01-01T10:00:00Z",
  verified_at: null,
  notice: "",
  content_available: true,
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ ...draft, to: draft.to[0], status: "uncertain" }),
    ),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function mount(value = draft) {
  await act(async () =>
    root.render(
      <OutlookReplyEditor
        accountKey="workspace:student"
        connection={connection}
        timezone="America/New_York"
        message={null}
        initialDraft={value}
        onChanged={vi.fn()}
      />,
    ),
  );
}
function button(text: string) {
  return [...host.querySelectorAll("button")].find(
    (b) => b.textContent === text,
  );
}
it("requires exact recipient review before send and submits the reviewed revision and hash", async () => {
  await mount();
  expect(fetch).not.toHaveBeenCalled();
  expect(button("Send this reply")?.disabled).toBe(true);
  expect(host.textContent).toContain("professor@example.com");
  await act(async () =>
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(),
  );
  await act(async () => {
    const send = button("Send this reply")!;
    send.click();
    send.click();
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(String(url)).toContain(`/drafts/${id}/send`);
  expect(JSON.parse(String(init?.body))).toMatchObject({
    expected_revision: 2,
    expected_payload_hash: draft.payload_hash,
    confirmed: true,
  });
  expect(host.textContent).not.toContain("Sent confirmed");
  expect(button("Send this reply")).toBeUndefined();
  expect(button("Check outcome")).toBeDefined();
});
it("a lost send response can only be checked and never exposes retry-send", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("network lost"));
  await mount();
  await act(async () =>
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(),
  );
  await act(async () => button("Send this reply")!.click());
  expect(
    sessionStorage.getItem(outlookSendKey("workspace:student", id)),
  ).not.toBeNull();
  expect(button("Send this reply")).toBeUndefined();
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ ...draft, to: draft.to[0], status: "uncertain" }),
  );
  await act(async () => button("Check outcome")!.click());
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).endsWith("/send")),
  ).toHaveLength(1);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).endsWith("/check")),
  ).toHaveLength(1);
  expect(host.textContent).toContain("does not send the message again");
});
it("restores the uncertainty barrier after reopening and only reports verified completion", async () => {
  sessionStorage.setItem(
    outlookSendKey("workspace:student", id),
    "pending-request",
  );
  vi.mocked(fetch).mockResolvedValue(
    Response.json({
      ...draft,
      to: draft.to[0],
      status: "completed",
      verified_at: "2026-10-24T20:00:00Z",
    }),
  );
  await mount();
  expect(button("Send this reply")).toBeUndefined();
  await act(async () => button("Check outcome")!.click());
  expect(host.textContent).toContain("Sent confirmed in Outlook");
  expect(
    sessionStorage.getItem(outlookSendKey("workspace:student", id)),
  ).toBeNull();
});
it("does not offer an expired draft for send", async () => {
  await mount({ ...draft, expires_at: "2020-01-01T10:00:00Z" });
  expect(button("Send this reply")).toBeUndefined();
  expect(host.textContent).toContain("review expired");
});
it("requires a new approval when refreshing an expired review changes its recipient", async () => {
  vi.mocked(fetch).mockResolvedValue(
    Response.json({
      ...draft,
      revision: 3,
      payload_hash: "b".repeat(64),
      to: "updated-professor@example.com",
    }),
  );
  await mount({ ...draft, expires_at: "2020-01-01T10:00:00Z" });
  await act(async () => button("Save and review reply")!.click());
  expect(vi.mocked(fetch).mock.calls[0]?.[1]?.method).toBe("PATCH");
  expect(
    JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body)),
  ).toMatchObject({
    expected_revision: 2,
    subject: draft.subject,
    body: draft.body,
  });
  expect(host.textContent).toContain("updated-professor@example.com");
  expect(
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked,
  ).toBe(false);
  expect(button("Send this reply")?.disabled).toBe(true);
});
it("never treats a completed status without a verified sent receipt as success", async () => {
  await mount({ ...draft, status: "completed", verified_at: null });
  expect(host.textContent).not.toContain("Sent confirmed");
  expect(button("Send this reply")).toBeUndefined();
  expect(button("Check outcome")).toBeDefined();
});
it("shows a content-cleared uncertain receipt without empty recipient or reply fields", async () => {
  await mount({
    ...draft,
    status: "uncertain",
    sender: "",
    to: [""],
    subject: "Reply content cleared",
    body: "",
    content_available: false,
    notice: "Reply content was cleared. You can still check the send outcome.",
  });
  expect(host.textContent).toContain("Reply content was cleared");
  expect(button("Check outcome")).toBeDefined();
  expect(button("Send this reply")).toBeUndefined();
  expect(host.querySelector("textarea")).toBeNull();
  expect(host.querySelector("input")).toBeNull();
  expect(host.textContent).not.toContain("To:");
});
