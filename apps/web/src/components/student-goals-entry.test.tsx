import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { productFeatures } from "../lib/product-features";
import { TodayWorkspace, type TodayWorkspaceView } from "./today-workspace";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

const original = productFeatures.studentGoals;
afterEach(() => {
  productFeatures.studentGoals = original;
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
describe("goals entry points", () => {
  it("keeps goals out of Today and Planner while disabled", () => {
    productFeatures.studentGoals = false;
    expect(render("today")).not.toContain('href="/goals"');
    expect(render("planner")).not.toContain('href="/goals"');
  });
  it("adds explicit review links without assuming personal activity when enabled", () => {
    productFeatures.studentGoals = true;
    const today = render("today"),
      planner = render("planner");
    expect(today).not.toContain("Your goals, at your pace");
    expect(today).toContain("Loading your day…");
    expect(today).not.toContain('href="/goals"');
    expect(planner).toContain("Review my goals");
    expect(planner).toContain("Tasks &amp; events");
    expect(today).not.toContain("You attended");
  });
});
