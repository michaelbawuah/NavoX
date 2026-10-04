import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AccountWorkspace } from "./account-workspace";

describe("initial account workspace HTML", () => {
  it("renders the public introduction before session restoration or JavaScript", () => {
    const html = renderToStaticMarkup(<AccountWorkspace />);

    expect(html).toMatch(/<h1[^>]*>NavoX<\/h1>/);
    expect(html).toContain(
      "Tasks, email, calendar plans, subscriptions, and news.",
    );
    expect(html).toContain("One personal workspace for your next move.");
    expect(html).toContain('href="/navox"');
    expect(html).not.toContain("Getting NavoX ready");
    expect(html).not.toContain('id="workspace-navigation"');
  });

  it.each(["connections", "inbox", "planner", "settings"] as const)(
    "waits for session restoration on the %s page",
    (view) => {
      const html = renderToStaticMarkup(<AccountWorkspace view={view} />);

      expect(html).toContain("Getting NavoX ready");
      expect(html).not.toContain('id="workspace-navigation"');
      expect(html).not.toContain("Your day.");
    },
  );
});
