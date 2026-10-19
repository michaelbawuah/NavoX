import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { productFeatures } from "../lib/product-features";
import { TodayWorkspace, type TodayWorkspaceView } from "./today-workspace";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

const original = productFeatures.studentEmail;
afterEach(() => {
  productFeatures.studentEmail = original;
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
describe("email review entry points", () => {
  it("keeps email-actions out of Today and Inbox while disabled", () => {
    productFeatures.studentEmail = false;
    expect(render("today")).not.toContain('href="/email-actions"');
    expect(render("inbox")).not.toContain('href="/email-actions"');
  });
  it("adds explicit review links without reading a message when enabled", () => {
    productFeatures.studentEmail = true;
    const today = render("today"),
      inbox = render("inbox");
    expect(today).toContain("One email, one next step");
    expect(today).toContain('href="/email-actions"');
    expect(inbox).toContain("Turn an email into a clear action");
    expect(inbox).toContain("Needs reply");
    expect(inbox).not.toContain("Analyze selected message");
  });
});
