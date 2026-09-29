import type { NewsStory } from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { newsStatus, newsStatusExplanation, safeNewsUrl } from "../lib/news";
import { NavoXNavigation, NavoXSourceList, NavoXStatus } from "./navox-ui";
import { NewsStoryCard } from "./news-workspace";

const story: NewsStory = {
  id: "story-one",
  headline: "A measured observation",
  description: "An attributed report.",
  category: "science",
  verification_status: "UNCONFIRMED",
  lifecycle_status: "DISCOVERED",
  source_count: 1,
  published_at: "2026-09-29T12:00:00Z",
  event_started_at: null,
  last_updated_at: "2026-09-29T12:00:00Z",
  retrieved_at: "2026-09-29T12:00:00Z",
  version: 1,
  sources: [
    {
      id: "item",
      source_id: "source",
      source_name: "Science Source",
      headline: "A measured observation",
      canonical_url: "https://science.example/report",
      description: null,
      published_at: "2026-09-29T12:00:00Z",
      updated_at: null,
      event_started_at: null,
      retrieved_at: "2026-09-29T12:00:00Z",
      last_observed_at: "2026-09-29T12:00:00Z",
      expires_at: "2026-10-01T12:00:00Z",
      revision: 1,
    },
  ],
  saved: false,
  followed: false,
  evidence_pending: true,
};

describe("News evidence display", () => {
  it("preserves uncertainty and keeps a card to two prominent actions", () => {
    const html = renderToStaticMarkup(
      createElement(NewsStoryCard, { story, onSave: vi.fn() }),
    );
    expect(html).toContain("Not confirmed");
    expect(html).toContain("Science Source");
    expect(html.match(/<button/g)).toHaveLength(1);
    expect(html.match(/<a /g)).toHaveLength(1);
    expect(html).not.toContain("VERIFIED");
    expect(html).not.toContain("confidence");
  });
  it("escapes source text and refuses executable citation links", () => {
    const html = renderToStaticMarkup(
      createElement(NavoXSourceList, {
        sources: [
          {
            ...story.sources[0],
            headline: "<script>alert(1)</script>",
            canonical_url: "javascript:alert(1)",
          },
        ],
      }),
    );
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("href=");
    for (const value of [
      "http://source.example",
      "javascript:alert(1)",
      "https://user:password@example.com",
      "bad",
    ])
      expect(safeNewsUrl(value)).toBeNull();
    expect(safeNewsUrl("https://science.example/report")).toBe(
      "https://science.example/report",
    );
  });
  it("states retraction and disagreement in words, not only color", () => {
    for (const state of ["RETRACTED", "DISPUTED", "ATTRIBUTED"] as const) {
      const html = renderToStaticMarkup(
        createElement(NavoXStatus, { status: state, explain: true }),
      );
      expect(html).toContain(newsStatus[state]);
      expect(html).toContain(newsStatusExplanation[state]);
    }
  });
  it("provides all four workspace destinations and marks the current page", () => {
    const html = renderToStaticMarkup(
      createElement(NavoXNavigation, { current: "News" }),
    );
    expect(html).toContain('href="/news" aria-current="page"');
    for (const label of ["Today", "NavoX", "News", "Subscriptions"])
      expect(html).toContain(label);
  });
});
