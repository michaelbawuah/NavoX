import type { EvidenceResource } from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ResultCard, SearchWorkspace } from "./search-workspace";

const resource: EvidenceResource = {
  source_type: "EMAIL",
  resource_id: "3d2b6b1a-0000-4000-8000-000000000001",
  title: "Quarterly planning review",
  excerpts: [
    {
      text: "The quarterly planning review covers the budget forecast.",
      start: 0,
      end: 55,
      chunk_index: 0,
      section_title: "Body",
    },
  ],
  canonical_url: "https://mail.example.com/message-1",
  source_updated_at: "2026-09-28T09:00:00Z",
  source_version: "etag-1",
  provenance: {
    connection_id: "9f0f6a1e-0000-4000-8000-0000000000aa",
    external_resource_id: "message-1",
    sensitivity: "PERSONAL",
  },
  origin: "CONNECTED",
};

describe("search result card", () => {
  it("shows the stored span with a safe link back to the source", () => {
    const markup = renderToStaticMarkup(
      createElement(ResultCard, { resource }),
    );
    expect(markup).toContain("Quarterly planning review");
    expect(markup).toContain("Email");
    expect(markup).toContain(
      "The quarterly planning review covers the budget forecast.",
    );
    expect(markup).toContain('href="https://mail.example.com/message-1"');
    expect(markup).toContain('target="_blank"');
    expect(markup).toContain('rel="noopener noreferrer"');
    expect(markup).toContain("opens in a new tab");
    expect(markup).not.toMatch(/score|similarity|relevance/i);
  });

  it("labels native records and omits a broken source link", () => {
    const markup = renderToStaticMarkup(
      createElement(ResultCard, {
        resource: {
          ...resource,
          source_type: "COMMITMENT",
          origin: "NATIVE",
          canonical_url: "javascript:alert(1)",
          provenance: {},
        },
      }),
    );
    expect(markup).toContain("Commitment");
    expect(markup).toContain("NavoX record");
    expect(markup).toContain("No source link available");
    expect(markup).not.toContain("javascript:");
  });

  it("offers opt-out controls only for known provenance", () => {
    const withButtons = renderToStaticMarkup(
      createElement(ResultCard, {
        resource,
        onExcludeSource: vi.fn(),
        onExcludeType: vi.fn(),
      }),
    );
    expect(withButtons).toContain("Hide this source");
    expect(withButtons).toContain("Hide email");
  });
});

describe("search workspace shell", () => {
  it("renders the usable first screen without implementation jargon", () => {
    const markup = renderToStaticMarkup(createElement(SearchWorkspace));
    expect(markup).toContain("Find what you need");
    expect(markup).toContain("What are you looking for?");
    expect(markup).toMatch(
      /<details[^>]*><summary>Filters &amp; options · All time<\/summary>/,
    );
    expect(markup).toMatch(/value="AUTO"/);
    expect(markup).toMatch(/<input[^>]*id="search-from"[^>]*value=""/);
    expect(markup).toMatch(/<input[^>]*id="search-to"[^>]*value=""/);
    expect(markup).toContain("Any type");
    expect(markup).toContain("Recent searches");
    expect(markup).toContain("Hidden from search");
    expect(markup).toContain('href="/navox/search"');
    expect(markup).toContain("Skip to search");
    expect(markup).toContain('aria-live="polite"');
    expect(markup).not.toMatch(/CANDIDATE_BOUND|RRF|vector|embedding|score/i);
  });
});
