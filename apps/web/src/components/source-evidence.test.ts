import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { EvidencePassages, SourceEvidence } from "./source-evidence";

describe("source evidence", () => {
  it("offers a deliberate read without fetching on initial render", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    try {
      const markup = renderToStaticMarkup(
        createElement(SourceEvidence, {
          evidenceId: "evidence-1",
          paused: false,
        }),
      );
      expect(markup).toContain("View source text");
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      fetchMock.mockRestore();
    }
  });

  it("blocks reads while paused", () => {
    const markup = renderToStaticMarkup(
      createElement(SourceEvidence, {
        evidenceId: "evidence-1",
        paused: true,
      }),
    );
    expect(markup).toContain('disabled=""');
    expect(markup).toContain("Resume NavoX");
  });

  it("renders source text as escaped quotes, never executable email HTML", () => {
    const markup = renderToStaticMarkup(
      createElement(EvidencePassages, {
        excerpts: [
          { source: "content", text: '<img src="x" onerror="alert(1)">' },
        ],
      }),
    );
    expect(markup).toContain("Email body");
    expect(markup).toContain("<blockquote>&lt;img");
    expect(markup).not.toContain("<img");
  });
});
