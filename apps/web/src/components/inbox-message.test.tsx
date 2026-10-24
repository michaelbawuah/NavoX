// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { inboxFixture } from "../test-fixtures/inbox";
import { InboxEmailPreview } from "./inbox-email-preview";
import { InboxMessage } from "./inbox-message";

let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      const path = String(url);
      const fixture = inboxFixture.find((row) =>
        path.includes(`/sources/${row.id}`),
      );
      if (!fixture) return Response.json({}, { status: 404 });
      return path.endsWith("/message")
        ? Response.json({
            ...fixture,
            source_id: fixture.sourceId,
            truncated: false,
          })
        : Response.json([
            { id: fixture.sourceId, label: "Synthetic QA source" },
          ]);
    }),
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

it("shows two explicitly synthetic provider-backed rows without sending, generating or using retained source text", async () => {
  await act(async () =>
    root.render(
      <>
        {inboxFixture.map((row) => (
          <InboxEmailPreview
            key={row.id}
            commitmentId={row.id}
            accountKey="workspace:student"
            title="Untrusted retained title"
            description="Saved summary"
          />
        ))}
      </>,
    ),
  );
  for (const row of inboxFixture) {
    expect(host.textContent).toContain(row.sender);
    expect(host.textContent).toContain(row.subject);
    expect(host.textContent).toContain(row.body);
  }
  expect(host.textContent).not.toContain("Untrusted retained title");
  expect(vi.mocked(fetch).mock.calls).toHaveLength(4);
  for (const [, options] of vi.mocked(fetch).mock.calls) {
    expect(options?.headers).toMatchObject({
      "X-NavoX-Account": "workspace:student",
    });
    expect(options?.method).toBeUndefined();
    expect(options?.cache).toBe("no-store");
  }
});

it("reads the selected live message and switches without showing the previous body", async () => {
  await act(async () =>
    root.render(
      <InboxMessage
        id={inboxFixture[0].id}
        accountKey="workspace:student"
        timezone="America/New_York"
      />,
    ),
  );
  expect(host.textContent).toContain(inboxFixture[0].body);
  expect(host.querySelector("a")?.getAttribute("href")).toBe(
    inboxFixture[0].open_url,
  );
  await act(async () =>
    root.render(
      <InboxMessage
        id={inboxFixture[1].id}
        accountKey="workspace:student"
        timezone="America/New_York"
      />,
    ),
  );
  expect(host.textContent).toContain(inboxFixture[1].body);
  expect(host.textContent).not.toContain(inboxFixture[0].body);
});

it("keeps failed provider details private and offers a retry", async () => {
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ detail: "internal provider stack" }, { status: 503 }),
  );
  await act(async () =>
    root.render(
      <InboxMessage
        id={inboxFixture[0].id}
        accountKey="workspace:student"
        timezone="America/New_York"
      />,
    ),
  );
  expect(host.textContent).toContain("This message couldn’t load.");
  expect(host.textContent).not.toContain("internal provider stack");
  const retry = host.querySelector("button")!;
  expect(retry.textContent).toBe("Retry");
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method)).toBe(
    true,
  );
});
