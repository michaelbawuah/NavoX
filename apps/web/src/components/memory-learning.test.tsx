// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  memoryLearningFixture,
  savedSuggestionFixture,
  suggestion,
} from "../test-fixtures/memory-learning";
import { MemoryLearningPanel } from "./memory-learning";

const mocks = vi.hoisted(() => ({
  call: null as null | {
    state: { callId: string };
    controller: { hangUp: () => Promise<void> };
  },
}));
vi.mock("./vox-call-provider", () => ({ useVoxCall: () => mocks.call }));
let root: Root, host: HTMLDivElement;
const onSaved = vi.fn(),
  onUncertain = vi.fn(),
  onBusyChange = vi.fn(),
  onPrivacyChanged = vi.fn();
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
async function mount(
  accountKey = "school:student",
  disabled = false,
  generation = 4,
) {
  await act(async () =>
    root.render(
      <MemoryLearningPanel
        accountKey={accountKey}
        generation={generation}
        disabled={disabled}
        onSaved={onSaved}
        onUncertain={onUncertain}
        onBusyChange={onBusyChange}
        onPrivacyChanged={onPrivacyChanged}
      />,
    ),
  );
}
function button(label: string) {
  const value = [...host.querySelectorAll("button")].find(
    (item) => item.textContent?.trim() === label,
  );
  if (!value) throw Error(`Missing ${label}`);
  return value;
}
async function click(label: string) {
  await act(async () => button(label).click());
}
async function agree() {
  const input = host.querySelector<HTMLInputElement>('input[type="checkbox"]');
  if (!input) throw Error("Missing independent private agreement");
  await act(async () => input.click());
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(Date.parse(suggestion.created_at));
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options?: RequestInit) =>
      options?.method === "POST"
        ? Response.json(savedSuggestionFixture(), { status: 201 })
        : Response.json(memoryLearningFixture()),
    ),
  );
  mocks.call = null;
  sessionStorage.clear();
  onSaved.mockReset();
  onUncertain.mockReset();
  onBusyChange.mockReset();
  onPrivacyChanged.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
it("only checks existing suggestions on request and requires independent exact private approval", async () => {
  await mount();
  expect(fetch).not.toHaveBeenCalled();
  await click("Check learning and suggestions");
  expect(host.textContent).toContain(suggestion.content);
  expect(host.textContent).toContain(
    "whole source conversation out of automatic voice recall",
  );
  expect(host.textContent).toContain("clears active chat context");
  expect(button("Save this suggestion").disabled).toBe(true);
  expect(
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked,
  ).toBe(false);
  await agree();
  expect(fetch).toHaveBeenCalledTimes(1);
  const closed = deferred<void>();
  const hangUp = vi.fn(() => closed.promise);
  mocks.call = { state: { callId: "synthetic-call" }, controller: { hangUp } };
  await mount();
  await click("Save this suggestion");
  expect(hangUp).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => closed.resolve());
  expect(onSaved).toHaveBeenCalledOnce();
  expect(onUncertain).not.toHaveBeenCalled();
  const [url, options] = vi.mocked(fetch).mock.calls[1];
  expect(String(url)).toMatch(
    new RegExp(`/learning/${suggestion.id}/confirm$`),
  );
  expect(options?.headers).toMatchObject({
    "X-NavoX-Account": "school:student",
  });
  expect(JSON.parse(String(options?.body))).toEqual({
    generation: 4,
    confirmation: "SAVE MEMORY",
    sensitive_agreement: true,
  });
  expect(String(url)).not.toContain(suggestion.content);
  expect(sessionStorage.length).toBe(0);
});
it("holds unknown save results for a fresh saved-state reload and never retries automatically", async () => {
  await mount();
  await click("Check learning and suggestions");
  await agree();
  vi.mocked(fetch).mockRejectedValueOnce(Error("lost response"));
  await click("Save this suggestion");
  expect(onUncertain).toHaveBeenCalledOnce();
  expect(button("Check learning and suggestions").disabled).toBe(true);
  expect(host.textContent).not.toContain(suggestion.content);
  await act(async () => vi.advanceTimersByTime(30000));
  expect(fetch).toHaveBeenCalledTimes(2);
  await mount("school:student", true);
  await mount("school:student", false);
  expect(button("Check learning and suggestions").disabled).toBe(false);
  expect(host.textContent).toContain("Saved memory reloaded");
  expect(onSaved).not.toHaveBeenCalled();
});
it("fences a held private list after account or memory generation changes", async () => {
  const held = deferred<Response>();
  vi.mocked(fetch).mockReturnValueOnce(held.promise);
  await mount();
  await click("Check learning and suggestions");
  await mount("school:new-owner", false, 5);
  await act(async () => held.resolve(Response.json(memoryLearningFixture())));
  expect(host.textContent).not.toContain(suggestion.content);
  expect(onSaved).not.toHaveBeenCalled();
  expect(button("Check learning and suggestions").disabled).toBe(false);
});
it("requests fresh privacy before showing suggestions from a newer generation", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    Response.json({ ...memoryLearningFixture(), generation: 5 }),
  );
  await mount();
  await click("Check learning and suggestions");
  expect(onPrivacyChanged).toHaveBeenCalledOnce();
  expect(host.textContent).not.toContain(suggestion.content);
  expect(onSaved).not.toHaveBeenCalled();
  expect(onUncertain).not.toHaveBeenCalled();
});
it("does not submit a private save after owner changes during local hang-up", async () => {
  const held = deferred<void>();
  mocks.call = {
    state: { callId: "synthetic-call" },
    controller: { hangUp: () => held.promise },
  };
  await mount();
  await click("Check learning and suggestions");
  await agree();
  await click("Save this suggestion");
  await mount("school:new-owner");
  await act(async () => held.resolve());
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(onSaved).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain(suggestion.content);
});
it("dismisses only on click without saving or forgetting the archived source", async () => {
  await mount();
  await click("Check learning and suggestions");
  vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 204 }));
  await click("Dismiss suggestion");
  const [url, options] = vi.mocked(fetch).mock.calls[1];
  expect(String(url)).toMatch(/\/dismiss$/);
  expect(JSON.parse(String(options?.body))).toEqual({ generation: 4 });
  expect(host.textContent).toContain("saved conversation was not deleted");
  expect(onSaved).not.toHaveBeenCalled();
  expect(onUncertain).not.toHaveBeenCalled();
});
it("expires an open suggestion without confirming it", async () => {
  await mount();
  await click("Check learning and suggestions");
  await agree();
  await act(async () => vi.advanceTimersByTime(7 * 86400000 + 1));
  expect(host.textContent).not.toContain(suggestion.content);
  expect(host.textContent).toContain("No current private suggestions");
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("permits manual review of an existing suggestion without claiming a qualified learning route", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    Response.json({
      ...memoryLearningFixture(),
      configured: false,
      status_reason: "unconfigured",
    }),
  );
  await mount();
  await click("Check learning and suggestions");
  expect(host.textContent).toContain("No qualified learning route");
  await agree();
  await click("Save this suggestion");
  expect(onSaved).toHaveBeenCalledOnce();
});
it("does not offer saving outside active disclosed recording", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    Response.json({
      ...memoryLearningFixture(),
      enabled: false,
      status_reason: "off_record",
    }),
  );
  await mount();
  await click("Check learning and suggestions");
  expect(host.textContent).toContain("paused while Off the record is on");
  expect(
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.disabled,
  ).toBe(true);
  expect(button("Save this suggestion").disabled).toBe(true);
});
it("treats a valid-looking wrong saved fact as an unknown result", async () => {
  await mount();
  await click("Check learning and suggestions");
  await agree();
  vi.mocked(fetch).mockResolvedValueOnce(
    Response.json({
      ...savedSuggestionFixture(),
      content: "Different source statement",
    }),
  );
  await click("Save this suggestion");
  expect(onSaved).not.toHaveBeenCalled();
  expect(onUncertain).toHaveBeenCalledOnce();
});
