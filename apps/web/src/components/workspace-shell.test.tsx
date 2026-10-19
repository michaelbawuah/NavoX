import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { drawerFocusControls, WorkspaceShell } from "./workspace-shell";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

describe("workspace navigation", () => {
  it("puts classes in student navigation and keeps optional products hidden", () => {
    const html = renderToStaticMarkup(
      <WorkspaceShell current="Classes">Timetable</WorkspaceShell>,
    );
    expect(html).toContain('href="/classes"');
    expect(
      html.match(
        /<a(?=[^>]*href="\/classes")(?=[^>]*aria-current="page")[^>]*>/g,
      ),
    ).toHaveLength(2);
    expect(html).not.toContain('href="/news"');
    expect(html).not.toContain('href="/subscriptions"');
    expect(html).not.toContain('href="/board"');
  });
  it("exposes whiteboard navigation only under its own release flag", async () => {
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_WHITEBOARD_ENABLED", "true");
    vi.resetModules();
    try {
      const { WorkspaceShell: EnabledShell } = await import(
        "./workspace-shell"
      );
      const html = renderToStaticMarkup(
        <EnabledShell current="Board">Practice lesson</EnabledShell>,
      );
      expect(html.match(/href="\/board"/g)).toHaveLength(1);
      expect(
        html.match(
          /<a(?=[^>]*href="\/board")(?=[^>]*aria-current="page")[^>]*>/g,
        ),
      ).toHaveLength(1);
      expect(html).not.toContain('href="/news"');
    } finally {
      vi.unstubAllEnvs();
      vi.resetModules();
    }
  });
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

describe("mobile drawer focus boundaries", () => {
  function accountNavigation(open: boolean) {
    const details = {
      querySelector: vi.fn(() => summary),
    };
    const makeControl = (inAccount: boolean, hasLayout = true) =>
      ({
        getClientRects: () => (hasLayout ? [{}] : []),
        closest: () => (inAccount && !open ? details : null),
      }) as unknown as HTMLElement;
    const close = makeControl(false);
    const summary = makeControl(true);
    // Chrome can report layout rectangles for content inside closed details.
    const signOut = makeControl(true);
    const hidden = makeControl(false, false);
    const navigation = {
      querySelectorAll: () => [close, summary, signOut, hidden],
    } as unknown as HTMLElement;
    return { navigation, close, summary, signOut };
  }

  it("wraps at the visible account summary when the disclosure is closed", () => {
    const { navigation, close, summary } = accountNavigation(false);
    expect(drawerFocusControls(navigation)).toEqual([close, summary]);
  });

  it("includes sign-out when the disclosure is open and excludes hidden controls", () => {
    const { navigation, close, summary, signOut } = accountNavigation(true);
    expect(drawerFocusControls(navigation)).toEqual([close, summary, signOut]);
  });
});
