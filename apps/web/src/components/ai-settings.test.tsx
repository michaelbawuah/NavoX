// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AISettings } from "./ai-settings";

const session = vi.hoisted(() => ({
  callbacks: null as null | {
    checking: () => void;
    restored: (value: unknown) => void;
    unavailable: () => void;
  },
  refresh: vi.fn(),
}));
vi.mock("../lib/account-session", () => ({
  observeAccountSession: (callbacks: NonNullable<typeof session.callbacks>) => {
    session.callbacks = callbacks;
    callbacks.checking();
    callbacks.restored({
      account: { id: "student", workspace: { id: "school" } },
    });
    return { dispose: vi.fn(), refresh: session.refresh };
  },
}));
const preferences = {
  configured: true,
  revision: 0,
  preferred_provider: "openai",
  allow_fallback: true,
  available_providers: ["openai"],
};
const activity = {
  runs: [
    {
      id: "run-1",
      provider: "openai",
      model: "owner-model",
      status: "SUCCEEDED",
      latency_ms: 50,
      estimated_cost: "0E-10",
      fallback_count: 0,
    },
  ],
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  session.refresh.mockReset();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      Response.json(url.endsWith("/activity") ? activity : preferences),
    ),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  session.callbacks = null;
  vi.unstubAllGlobals();
});
async function mount(accountKey = "school:student") {
  await act(async () => root.render(<AISettings accountKey={accountKey} />));
}
function button(label: string) {
  const result = [...host.querySelectorAll("button")].find(
    (node) => node.textContent === label,
  );
  if (!result) throw Error(`Missing ${label}`);
  return result;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

it("binds both private reads and writes to the displayed account", async () => {
  await mount();
  expect(host.textContent).toContain("owner-model");
  expect(host.textContent).toContain("$0.0000 USD");
  const checkbox = host.querySelector(
    'input[type="checkbox"]',
  ) as HTMLInputElement;
  await act(async () => checkbox.click());
  await act(async () => button("Save preferences").click());
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3);
  for (const [, options] of vi.mocked(fetch).mock.calls) {
    expect(options?.credentials).toBe("include");
    expect(options?.cache).toBe("no-store");
    expect(new Headers(options?.headers).get("X-NavoX-Account")).toBe(
      "school:student",
    );
  }
  const write = vi
    .mocked(fetch)
    .mock.calls.find(([, options]) => options?.method === "PATCH");
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
    expected_revision: 0,
    allow_fallback: false,
  });
});

it("does not publish a late private read after account checking or a cookie switch", async () => {
  const reads = [deferred<Response>(), deferred<Response>()];
  let count = 0;
  vi.mocked(fetch).mockImplementation(() => reads[count++].promise);
  await mount();
  act(() => {
    session.callbacks?.checking();
    session.callbacks?.restored({
      account: { id: "other", workspace: { id: "school" } },
    });
  });
  await act(async () => {
    reads[0].resolve(Response.json(preferences));
    reads[1].resolve(Response.json(activity));
  });
  expect(host.textContent).not.toContain("owner-model");
  expect(host.querySelector("select")).toBeNull();
  expect(host.textContent).toContain("Your account changed");
});

it("rejects a captured-account response before exposing either settings or activity", async () => {
  vi.mocked(fetch).mockImplementation(async (url) =>
    String(url).endsWith("/activity")
      ? Response.json(activity)
      : Response.json({ detail: "Account changed" }, { status: 409 }),
  );
  await mount();
  expect(host.textContent).not.toContain("owner-model");
  expect(host.querySelector("select")).toBeNull();
  expect(host.textContent).toContain("AI settings could not be loaded");
});

it("fences a pending save and clears private content when the session changes", async () => {
  const write = deferred<Response>();
  vi.mocked(fetch).mockImplementation(async (url, options) =>
    options?.method === "PATCH"
      ? write.promise
      : Response.json(
          String(url).endsWith("/activity") ? activity : preferences,
        ),
  );
  await mount();
  await act(async () => {
    button("Save preferences").click();
    session.callbacks?.checking();
  });
  expect(host.textContent).not.toContain("owner-model");
  expect(host.querySelector("select")).toBeNull();
  await act(async () =>
    write.resolve(Response.json({ ...preferences, revision: 7 })),
  );
  expect(host.textContent).not.toContain("AI preferences saved");
  expect(host.querySelector("select")).toBeNull();
});

it("requires a fresh reload after an uncertain save and never submits it twice", async () => {
  const write = deferred<Response>();
  vi.mocked(fetch).mockImplementation(async (url, options) =>
    options?.method === "PATCH"
      ? write.promise
      : Response.json(
          String(url).endsWith("/activity") ? activity : preferences,
        ),
  );
  await mount();
  await act(async () => {
    const save = button("Save preferences");
    save.click();
    save.click();
  });
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([, options]) => options?.method === "PATCH"),
  ).toHaveLength(1);
  await act(async () =>
    write.resolve(new Response("Unavailable", { status: 503 })),
  );
  expect(host.querySelector("select")).toBeNull();
  expect(host.textContent).toContain(
    "Reload AI preferences before saving again",
  );
  await act(async () => button("Reload AI preferences").click());
  expect(session.refresh).toHaveBeenCalledTimes(1);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([, options]) => options?.method === "PATCH"),
  ).toHaveLength(1);
});
