// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { saveMemoryForgetDraft } from "../lib/vox-memory-review";
import { MemoryPrivacyPanel } from "./memory-privacy";

const session = vi.hoisted(() => ({
  callbacks: null as null | {
    checking: () => void;
    restored: (value: unknown) => void;
    unavailable: () => void;
  },
}));
vi.mock("../lib/account-session", () => ({
  observeAccountSession: (callbacks: typeof session.callbacks) => {
    session.callbacks = callbacks;
    callbacks?.restored({
      account: { id: "student", workspace: { id: "school" } },
    });
    return { dispose: vi.fn() };
  },
  notifyAccountSessionChanging: vi.fn(() => ({ id: "change" })),
  notifyAccountSessionChanged: vi.fn(),
}));
vi.mock("./personal-memory", () => ({
  PersonalMemoryPanel: () => <p>Writing preferences</p>,
}));
const privacy = {
  history_enabled: false,
  retention_days: null,
  off_the_record: false,
  generation: 4,
  disclosure_version: null,
};
const fact = {
  id: "fact-1",
  label: "Study preference",
  content: "I focus in the morning.",
  source_kind: "vox",
  source_conversation_id: "call-1",
  created_at: "2026-10-26T12:00:00Z",
  updated_at: "2026-10-26T12:00:00Z",
  sensitive: false,
};
const call = {
  id: "call-1",
  title: "Morning plan",
  source_kind: "vox",
  created_at: fact.created_at,
  updated_at: fact.updated_at,
  message_count: 2,
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState({}, "", "/settings");
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === "DELETE")
        return new Response(null, { status: 204 });
      if (options?.method === "PUT")
        return Response.json({
          ...privacy,
          ...JSON.parse(String(options.body)),
          disclosure_version: 1,
        });
      if (url.includes("/privacy")) return Response.json(privacy);
      if (url.endsWith("/facts")) return Response.json([fact]);
      if (url.includes("/history?")) return Response.json([call]);
      return Response.json({
        ...call,
        turns: [
          {
            role: "user",
            content: "Plan my morning",
            created_at: call.created_at,
          },
        ],
        summary: null,
      });
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
  session.callbacks = null;
});
async function mount() {
  await act(async () =>
    root.render(
      <MemoryPrivacyPanel
        accountKey="school:student"
        timezone="America/New_York"
      />,
    ),
  );
}
function button(label: string) {
  const result = [...host.querySelectorAll("button")].find(
    (node) => node.textContent?.trim() === label,
  );
  if (!result) throw Error(`Missing ${label}`);
  return result;
}
async function click(label: string) {
  await act(async () => button(label).click());
}
async function select(value: string) {
  await act(async () => {
    const input = host.querySelector("select");
    if (!input) throw Error("Missing retention choice");
    input.value = value;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
async function type(input: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
it("does not preselect retention and requires separate disclosure before history starts", async () => {
  await mount();
  expect(host.querySelector("select")?.value).toBe("");
  expect(host.textContent).toContain("From a Vox call");
  await select("never");
  expect(button("Save history choice").disabled).toBe(true);
  const checkbox = host.querySelector(
    'input[type="checkbox"]',
  ) as HTMLInputElement;
  await act(async () => checkbox.click());
  expect(button("Save history choice").disabled).toBe(false);
  await click("Save history choice");
  const writes = vi
    .mocked(fetch)
    .mock.calls.filter(([, options]) => options?.method === "PUT");
  expect(writes).toHaveLength(1);
  expect(JSON.parse(String(writes[0][1]?.body))).toEqual({
    history_enabled: true,
    retention_days: null,
    off_the_record: false,
    generation: 4,
    disclosure_accepted: true,
  });
});
it("off-record changes preserve saved retention instead of an unapproved draft", async () => {
  await mount();
  await select("90");
  await click("Off");
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([, options]) => options?.method === "PUT");
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    history_enabled: false,
    retention_days: null,
    off_the_record: true,
    disclosure_accepted: false,
  });
});
it("requires exact review before forgetting a conversation and uses its source id", async () => {
  await mount();
  await click("View and search history");
  const forget = [...host.querySelectorAll("button")].find((node) =>
    node.textContent?.startsWith("Forget Morning plan"),
  );
  if (!forget) throw Error("Missing conversation forget");
  await act(async () => forget.click());
  expect(host.textContent).toContain("every fact learned from it");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([, options]) => options?.method === "DELETE"),
  ).toBe(false);
  await click("Yes, forget it");
  const removal = vi
    .mocked(fetch)
    .mock.calls.find(([, options]) => options?.method === "DELETE");
  expect(removal?.[0]).toMatch(/\/history\/call-1$/);
  expect(removal?.[1]?.headers).toMatchObject({
    "X-NavoX-Account": "school:student",
  });
});
it("blocks delete-all until the exact phrase is entered", async () => {
  await mount();
  await click("Delete all memory");
  expect(button("Permanently delete everything").disabled).toBe(true);
  await type(
    host.querySelector(
      'input[aria-label="Type DELETE EVERYTHING to confirm"]',
    ) as HTMLInputElement,
    "DELETE EVERYTHING",
  );
  expect(button("Permanently delete everything").disabled).toBe(false);
  await click("Permanently delete everything");
  const removal = vi
    .mocked(fetch)
    .mock.calls.find(([, options]) => options?.method === "DELETE");
  expect(JSON.parse(String(removal?.[1]?.body))).toEqual({
    generation: 4,
    confirmation: "DELETE EVERYTHING",
  });
});
it("clears private data and fences a delayed history response when the session changes", async () => {
  await mount();
  let resolve: (response: Response) => void = () => {};
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  await act(async () => button("View and search history").click());
  await act(async () => session.callbacks?.checking());
  await act(async () => resolve(Response.json([call])));
  expect(host.textContent).not.toContain("Morning plan");
  expect(host.textContent).not.toContain(fact.content);
  expect(host.querySelector("select")).toBeNull();
});
it("fences further writes after an uncertain mutation until the saved state is loaded", async () => {
  await mount();
  await click("Add something");
  await type(
    host.querySelector('input[maxlength="80"]') as HTMLInputElement,
    "My goal",
  );
  const textarea = host.querySelector("textarea");
  if (!textarea) throw Error("Missing fact content");
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )?.set?.call(textarea, "Learn circuits");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  vi.mocked(fetch).mockRejectedValueOnce(new Error("network lost"));
  await click("Save fact");
  expect(button("Add something").disabled).toBe(true);
  expect(host.textContent).toContain("Reload your memory");
  await click("Retry");
  expect(button("Add something").disabled).toBe(false);
});

it("warns that correcting a source-linked fact also forgets its original source", async () => {
  await mount();
  const edit = [...host.querySelectorAll("button")].find((node) =>
    node.textContent?.startsWith("Edit Study preference"),
  );
  if (!edit) throw new Error("Missing fact edit");
  await act(async () => edit.click());
  expect(host.textContent).toContain("also forgets its original conversation");
  expect(button("Save correction and forget source").disabled).toBe(false);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([, options]) => options?.method === "PATCH"),
  ).toBe(false);
});

it("opens an owner-bound Vox search without deleting or putting its subject in the URL", async () => {
  saveMemoryForgetDraft(sessionStorage, "school:student", "morning");
  await mount();
  expect(host.textContent).toContain("Review matches for “morning”");
  expect(
    (host.querySelector('input[type="search"]') as HTMLInputElement).value,
  ).toBe("morning");
  expect(
    host.querySelector('[data-memory-match="true"]')?.textContent,
  ).toContain("Study preference");
  expect(window.location.href).not.toContain("morning");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([url]) =>
        String(url).endsWith("/history?query=morning"),
      ),
  ).toBe(true);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([, options]) => options?.method === "DELETE"),
  ).toBe(false);
});
