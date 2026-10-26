// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SettingsNavigation } from "./settings-navigation";

let root: Root, host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  window.history.replaceState({}, "", "/settings#memory-privacy");
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
it("links every settings section and clearly identifies the selected one", async () => {
  await act(async () => root.render(<SettingsNavigation />));
  expect(host.querySelectorAll("a")).toHaveLength(8);
  expect(host.querySelector('[aria-current="location"]')?.textContent).toBe(
    "Memory & privacy",
  );
  const appearance = host.querySelector(
    'a[href="#appearance"]',
  ) as HTMLAnchorElement;
  await act(async () => appearance.click());
  expect(host.querySelector('[aria-current="location"]')?.textContent).toBe(
    "Appearance",
  );
});
