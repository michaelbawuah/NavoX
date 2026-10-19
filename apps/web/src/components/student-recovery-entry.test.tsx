import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { productFeatures } from "../lib/product-features";
import { TodayWorkspace, type TodayWorkspaceView } from "./today-workspace";

const original = productFeatures.studentRecovery;
afterEach(() => {
  productFeatures.studentRecovery = original;
});
function render(view: TodayWorkspaceView) {
  return renderToStaticMarkup(
    createElement(TodayWorkspace, {
      view,
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
describe("recovery entry points", () => {
  it("keeps recovery out of Today and Planner while disabled", () => {
    productFeatures.studentRecovery = false;
    expect(render("today")).not.toContain('href="/recovery"');
    expect(render("planner")).not.toContain('href="/recovery"');
  });
  it("adds explicit review links without predicting missed classes when enabled", () => {
    productFeatures.studentRecovery = true;
    const today = render("today"),
      planner = render("planner");
    expect(today).toContain("Catch up at your pace");
    expect(today).toContain('href="/recovery"');
    expect(planner).toContain("Review a catch-up proposal");
    expect(planner).toContain("Tasks &amp; events");
    expect(today).not.toContain("You missed class");
  });
});
