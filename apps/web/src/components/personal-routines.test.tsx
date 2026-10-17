import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PersonalRoutines, RoutineResultView } from "./personal-routines";

afterEach(() => vi.unstubAllGlobals());

describe("personal routine interface", () => {
  it("renders clear templates and no enabled routine or network mutation during render", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const html = renderToStaticMarkup(
      <PersonalRoutines timezone="America/New_York" />,
    );
    expect(fetch).not.toHaveBeenCalled();
    expect(html).toContain('id="personal-routines"');
    expect(html).toContain("Morning priorities");
    expect(html).toContain("Friday catch-up");
    expect(html).toContain("No-reply check");
    expect(html).toContain("Preview once");
    expect(html).toMatch(/disabled=""[^>]*>Enable this routine/);
    expect(html).toContain("Loading your routines");
    expect(html).not.toContain("No routines yet");
    expect(html).toContain(
      "This does not refresh connected apps or send email.",
    );
    expect(html).toContain("America/New_York");
  });

  it("explains the global pause without claiming scheduled checks will run", () => {
    const html = renderToStaticMarkup(
      <PersonalRoutines paused timezone="UTC" />,
    );
    expect(html).toContain("Routines will skip their checks");
    expect(html).toContain("You can still pause or delete a routine");
    expect(html).toMatch(/disabled=""[^>]*>Preview once/);
    expect(html).toMatch(/disabled=""[^>]*>Enable this routine/);
  });

  it("shows the actual saved-result timestamp and review destinations, escaping source titles", () => {
    const html = renderToStaticMarkup(
      <RoutineResultView
        timezone="UTC"
        result={{
          checked_at: "2026-10-17T12:00:00Z",
          basis: "saved_data",
          headline: "One watched thread needs review.",
          items: [
            {
              id: "watch-1",
              title: "<script>source text</script>",
              status: "due",
              due_at: null,
              kind: "thread_watch",
            },
          ],
          notice: "This preview does not check Gmail for new replies.",
        }}
      />,
    );
    expect(html).toContain("Saved data checked");
    expect(html).toContain('href="/inbox#thread-watches"');
    expect(html).toContain("&lt;script&gt;source text&lt;/script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("does not check Gmail");
  });

  it("treats no matching saved items as an honest result", () => {
    const html = renderToStaticMarkup(
      <RoutineResultView
        result={{
          checked_at: "2026-10-17T12:00:00Z",
          basis: "saved_data",
          headline: "No saved items match this routine right now.",
          items: [],
          notice: "Connected apps were not refreshed.",
        }}
      />,
    );
    expect(html).toContain("No matching saved items right now");
    expect(html).toContain("Connected apps were not refreshed");
    expect(html).not.toContain("caught up");
  });
});
