// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { loadGoogleConnections } from "./account-access";
import { useInboxReplyCount } from "./inbox-reply-count";
import { loadOutlookReplySummary } from "./outlook-summary";

vi.mock("./account-access", () => ({ loadGoogleConnections: vi.fn() }));
vi.mock("./outlook-summary", () => ({ loadOutlookReplySummary: vi.fn() }));
const emptyOutlook = {
  connected: false,
  messages: [],
  checked_mailboxes: 0,
  available_mailboxes: 0,
  limited: false,
  unavailable: false,
};
const connection = {
  id: "google",
  provider: "google" as const,
  external_email: "qa@example.test",
  status: "active" as const,
  granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
  last_checked_at: null,
  last_error: null,
};
const item = {
  id: "reply",
  title: "Synthetic reply task",
  type: "follow_up",
  status: "active",
  due_at: null,
  sources: [
    {
      provider: "google",
      source_type: "gmail_message",
      connection_id: "google",
      external_resource_id: "qa-message",
    },
  ],
};
const feed = (rows: unknown[]) => ({
  completed_recently: [],
  needs_attention: rows,
  coming_up: [],
  waiting_on: [],
});
let host: HTMLDivElement, root: Root;
function Count({ owner, supplied }: { owner: string; supplied?: number }) {
  return (
    <output>{useInboxReplyCount(owner, supplied, "America/New_York")}</output>
  );
}
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.mocked(loadGoogleConnections).mockResolvedValue([connection]);
  vi.mocked(loadOutlookReplySummary).mockResolvedValue(emptyOutlook);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(feed([item]))),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
it("counts connected Gmail actions using the student's timezone, and hides disconnected sources", async () => {
  await act(async () => root.render(<Count owner="workspace:student" />));
  expect(host.textContent).toBe("1");
  expect(vi.mocked(fetch).mock.calls[0][0]).toContain(
    "timezone=America%2FNew_York",
  );
  vi.mocked(loadGoogleConnections).mockResolvedValue([
    { ...connection, status: "needs_reauthorization" },
  ]);
  await act(async () => root.render(<Count owner="workspace:other" />));
  expect(host.textContent).toBe("0");
});
it("a late result from the previous account cannot replace the new account's count", async () => {
  let resolve!: (response: Response) => void;
  vi.mocked(fetch)
    .mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    )
    .mockResolvedValueOnce(Response.json(feed([])));
  await act(async () => root.render(<Count owner="workspace:old" />));
  await act(async () => root.render(<Count owner="workspace:new" />));
  expect(host.textContent).toBe("0");
  await act(async () => resolve(Response.json(feed([item]))));
  expect(host.textContent).toBe("0");
});
it("reuses Today's loaded count without a second feed request", async () => {
  await act(async () =>
    root.render(<Count owner="workspace:student" supplied={3} />),
  );
  expect(host.textContent).toBe("3");
  expect(fetch).not.toHaveBeenCalled();
  expect(loadOutlookReplySummary).not.toHaveBeenCalled();
});
it("adds only checked Outlook candidates to the existing Gmail count", async () => {
  vi.mocked(loadOutlookReplySummary).mockResolvedValue({
    ...emptyOutlook,
    connected: true,
    checked_mailboxes: 1,
    available_mailboxes: 4,
    limited: true,
    unavailable: true,
    messages: [
      {
        id: "source",
        connection_id: "mailbox",
        sender: "professor@example.test",
        subject: "Reply needed",
        preview: "",
        reason: "Asked for a reply",
        sent_at: null,
        open_url: null,
      },
    ],
  });
  await act(async () => root.render(<Count owner="workspace:student" />));
  expect(host.textContent).toBe("2");
});
it("retains checked Outlook candidates if the Google feed is unavailable", async () => {
  vi.mocked(loadGoogleConnections).mockRejectedValue(new Error("unavailable"));
  vi.mocked(loadOutlookReplySummary).mockResolvedValue({
    ...emptyOutlook,
    connected: true,
    checked_mailboxes: 1,
    available_mailboxes: 1,
    messages: [
      {
        id: "source",
        connection_id: "mailbox",
        sender: "professor@example.test",
        subject: "Reply needed",
        preview: "",
        reason: "Asked for a reply",
        sent_at: null,
        open_url: null,
      },
    ],
  });
  await act(async () => root.render(<Count owner="workspace:student" />));
  expect(host.textContent).toBe("1");
});
