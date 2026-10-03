import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkspaceShell } from "./workspace-shell";

describe("workspace navigation", () => {
  it("marks only the current destination in the main navigation", () => {
    const html = renderToStaticMarkup(
      <WorkspaceShell current="Inbox">Messages</WorkspaceShell>,
    );
    const mainNavigation = html.match(
      /<nav[^>]*aria-label="Main navigation"[^>]*>([\s\S]*?)<\/nav>/,
    )?.[1];
    expect(mainNavigation).toBeDefined();
    expect(mainNavigation?.match(/aria-current="page"/g)).toHaveLength(1);
    const activeLink = mainNavigation?.match(
      /<a[^>]*aria-current="page"[^>]*>/,
    )?.[0];
    expect(activeLink).toContain('href="/inbox"');
  });

  it("keeps the page content in one reachable landmark", () => {
    const html = renderToStaticMarkup(
      <WorkspaceShell current="Planner">
        <section>Upcoming events</section>
      </WorkspaceShell>,
    );
    expect(html.match(/<main\b/g)).toHaveLength(1);
    expect(html).toContain('href="#workspace-content"');
    expect(html).toContain('id="workspace-content"');
    expect(html.match(/Upcoming events/g)).toHaveLength(1);
    expect(html).toContain('aria-controls="workspace-navigation"');
    expect(html).toContain('aria-expanded="false"');
  });
});
