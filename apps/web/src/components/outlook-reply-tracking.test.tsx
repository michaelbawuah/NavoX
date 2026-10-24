// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readOutlookDraft } from "../lib/outlook";
import { OutlookReplyTracking } from "./outlook-reply-tracking";

const id = "11111111-1111-4111-8111-111111111111";
const draft = readOutlookDraft({
  id,
  revision: 1,
  payload_hash: "a".repeat(64),
  connection_id: id,
  source_message_id: "source",
  sender: "owner@example.com",
  to: "teacher@example.com",
  subject: "Reply",
  body: "Thank you",
  status: "completed",
  expires_at: null,
  verified_at: "2026-10-24T10:01:00Z",
  sent_at: "2026-10-24T10:00:00Z",
  reply_state: "unknown",
  reply_scan_complete: false,
  reply_notice: "A later reply could not be confirmed.",
});
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it("does not turn incomplete reply evidence into Waiting or send another email", async () => {
  const changed = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ ...draft, to: draft.to, reply_scan_count: 50 }),
    ),
  );
  await act(async () =>
    root.render(
      <OutlookReplyTracking
        accountKey="workspace:owner"
        draft={draft}
        timezone="UTC"
        onChanged={changed}
      />,
    ),
  );
  expect(fetch).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Reply status needs checking");
  expect(host.textContent).not.toContain("Waiting for a reply");
  await act(async () => host.querySelector("button")!.click());
  expect(changed).toHaveBeenCalledWith(
    expect.objectContaining({ reply_state: "unknown" }),
  );
  expect(vi.mocked(fetch).mock.calls[0][0]).toMatch(/\/check-replies$/);
});

it("discards a delayed private result after an account switch", async () => {
  const changed = vi.fn();
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
  await act(async () =>
    root.render(
      <OutlookReplyTracking
        accountKey="workspace:old"
        draft={draft}
        timezone="UTC"
        onChanged={changed}
      />,
    ),
  );
  await act(async () => host.querySelector("button")!.click());
  await act(async () =>
    root.render(
      <OutlookReplyTracking
        accountKey="workspace:new"
        draft={draft}
        timezone="UTC"
        onChanged={changed}
      />,
    ),
  );
  await act(async () =>
    finish(
      Response.json({
        ...draft,
        reply_state: "replied",
        reply_received_at: "2026-10-24T10:03:00Z",
      }),
    ),
  );
  expect(changed).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain("Reply found");
});
