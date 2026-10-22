import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { productFeatures } from "../lib/product-features";
import { TodayWorkspace } from "./today-workspace";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));
const original = productFeatures.studentCalendarAvailability;
afterEach(() => {
  productFeatures.studentCalendarAvailability = original;
});
function render() {
  return renderToStaticMarkup(
    createElement(TodayWorkspace, {
      view: "connections",
      account: {
        id: "11111111-1111-4111-8111-111111111111",
        email: "student@example.test",
        display_name: "Student",
        timezone: "UTC",
        workspace: {
          id: "22222222-2222-4222-8222-222222222222",
          name: "Personal",
          workspace_type: "personal",
        },
      },
      connections: [],
      message: "",
      onConnectionsChanged: vi.fn(),
      onSignOut: vi.fn(),
    }),
  );
}
describe("calendar purpose consent entry", () => {
  it("keeps calendar purpose setup hidden when its independent feature flag is off", () => {
    productFeatures.studentCalendarAvailability = false;
    expect(render()).not.toContain("Find time without sharing event details");
  });
  it("shows the separate truthful consent entry without starting a provider read during render", () => {
    productFeatures.studentCalendarAvailability = true;
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render();
      expect(html).toContain("Find time without sharing event details");
      expect(html).toContain("Outlook / Microsoft 365");
      expect(html).toContain("Apple / iCloud");
      expect(html).toContain("Unavailable here");
      expect(html).toContain("Access not checked");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
});
