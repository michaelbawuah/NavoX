import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GmailRecheck, RecheckRow } from "./gmail-recheck";

describe("older Gmail review", () => {
  it("starts collapsed and makes no provider or metadata requests on render", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    try {
      const markup = renderToStaticMarkup(
        createElement(GmailRecheck, {
          connectionId: "connection",
          disabled: false,
          onRefresh: vi.fn(),
        }),
      );
      expect(markup).toContain("Review older Gmail items");
      expect(markup).toContain('aria-expanded="false"');
      expect(markup).not.toContain("Remove selected");
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      fetchMock.mockRestore();
    }
  });

  it.each(["retained", "needs_review", "failed", "checking"])(
    "does not offer removal for %s",
    (outcome) => {
      const markup = renderToStaticMarkup(
        createElement(RecheckRow, {
          item: {
            commitment_id: "a",
            title: "Saved item",
            outcome,
            reason: "matching_action",
            preview_id: "preview",
            checked_at: null,
          },
          selected: false,
          disabled: false,
          selectionFull: false,
          onSelect: vi.fn(),
          onKeep: vi.fn(),
          onRetry: vi.fn(),
        }),
      );
      expect(markup).not.toContain('type="checkbox"');
    },
  );

  it("renders suggestions unselected, escapes saved titles, and offers Keep", () => {
    const markup = renderToStaticMarkup(
      createElement(RecheckRow, {
        item: {
          commitment_id: "a",
          title: "<img src=x onerror=alert(1)>",
          outcome: "remove_suggested",
          reason: "no_action_found",
          preview_id: "preview",
          checked_at: null,
        },
        selected: false,
        disabled: false,
        selectionFull: false,
        onSelect: vi.fn(),
        onKeep: vi.fn(),
        onRetry: vi.fn(),
      }),
    );
    expect(markup).toContain('type="checkbox"');
    expect(markup).not.toContain('checked=""');
    expect(markup).toContain("Keep this item");
    expect(markup).toContain("Review before removing");
    expect(markup).toContain("&lt;img");
    expect(markup).not.toContain("<img");
  });
});
