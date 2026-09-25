import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PagedList } from "./paged-list";

describe("bounded Today lists", () => {
  it("renders six of fifty-two suggestions and provides page navigation", () => {
    const markup = renderToStaticMarkup(
      createElement(PagedList, {
        label: "Suggestions",
        items: Array.from({ length: 52 }, (_, i) =>
          createElement("article", { key: i }, `Suggestion ${i + 1}`),
        ),
      }),
    );
    expect(markup.match(/<article>/g)).toHaveLength(6);
    expect(markup).toContain("1–6 of 52");
    expect(markup).not.toContain("Suggestion 7");
    expect(markup).toContain('aria-label="Suggestions pages"');
    expect(markup).toContain("Next");
  });
});
