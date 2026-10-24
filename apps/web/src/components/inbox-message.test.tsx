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
  const onReadSource = vi.fn();
  await act(async () =>
    root.render(
      <InboxMessage
        id={inboxFixture[0].id}
        accountKey="workspace:student"
        timezone="America/New_York"
        onReadSource={onReadSource}
      />,
    ),
  );
  expect(host.textContent).toContain(inboxFixture[0].body);
  expect(onReadSource).toHaveBeenLastCalledWith(
    expect.objectContaining({
      accountKey: "workspace:student",
      commitmentId: inboxFixture[0].id,
      sourceId: inboxFixture[0].sourceId,
      body: inboxFixture[0].body,
      sentAt: inboxFixture[0].sent_at,
    }),
  );
  expect(host.querySelector("a")?.getAttribute("href")).toBe(
    inboxFixture[0].open_url,
  );
  await act(async () =>
    root.render(
      <InboxMessage
        id={inboxFixture[1].id}
        accountKey="workspace:student"
        timezone="America/New_York"
        onReadSource={onReadSource}
      />,
    ),
  );
  expect(host.textContent).toContain(inboxFixture[1].body);
  expect(host.textContent).not.toContain(inboxFixture[0].body);
  expect(onReadSource).toHaveBeenCalledWith(null);
  expect(onReadSource).toHaveBeenLastCalledWith(
    expect.objectContaining({
      sourceId: inboxFixture[1].sourceId,
      body: inboxFixture[1].body,
    }),
  );
});

it("never forwards a late provider body from the previous owner into meeting review", async () => {
  let finish!: (value: Response) => void;
  const onReadSource = vi.fn();
  const fixture = inboxFixture[0];
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (!String(url).endsWith("/message"))
      return Response.json([
        { id: fixture.sourceId, label: "Synthetic QA source" },
      ]);
    if (
      new Headers(init?.headers).get("X-NavoX-Account") === "workspace:student"
    )
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    return Response.json({
      ...fixture,
      body: "New owner source",
      source_id: fixture.sourceId,
      truncated: false,
    });
  });
  await act(async () =>
    root.render(
      <InboxMessage
        id={fixture.id}
        accountKey="workspace:student"
        timezone="America/New_York"
        onReadSource={onReadSource}
      />,
    ),
  );
  await act(async () =>
    root.render(
      <InboxMessage
        id={fixture.id}
        accountKey="workspace:new"
        timezone="America/New_York"
        onReadSource={onReadSource}
      />,
    ),
  );
  await act(async () =>
    finish(
      Response.json({
        ...fixture,
        body: "Late private body",
        source_id: fixture.sourceId,
        truncated: false,
      }),
    ),
  );
  expect(host.textContent).toContain("New owner source");
  expect(host.textContent).not.toContain("Late private body");
  expect(
    onReadSource.mock.calls.every(
      ([value]) => value === null || value.accountKey === "workspace:new",
    ),
  ).toBe(true);
  expect(onReadSource).toHaveBeenLastCalledWith(
    expect.objectContaining({
      accountKey: "workspace:new",
      body: "New owner source",
    }),
  );
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
