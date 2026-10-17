import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { PersonalMemoryPanel, SavedMemoryPicker } from "./personal-memory";

describe("saved preference controls", () => {
  it("keeps loading distinct from an empty memory list", () => {
    const html = renderToStaticMarkup(createElement(PersonalMemoryPanel));
    expect(html).toContain("Loading saved preferences");
    expect(html).toContain('aria-busy="true"');
    expect(html).not.toContain("Start with how you like replies written");
    expect(html).toContain("You save these yourself");
  });
  it("does not load or apply context before the user opens the picker", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const change = vi.fn();
    try {
      const html = renderToStaticMarkup(
        createElement(SavedMemoryPicker, {
          value: "My question",
          onChange: change,
          maxLength: 500,
        }),
      );
      expect(html).toContain("Use saved preferences");
      expect(html).toContain('aria-expanded="false"');
      expect(html).not.toContain("Add to this message");
      expect(fetch).not.toHaveBeenCalled();
      expect(change).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
});
