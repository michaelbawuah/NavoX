import type { NewsAnswer, NewsStory, NewsStorySummary } from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { newsStatus, newsStatusExplanation, safeNewsUrl } from "../lib/news";
import { NavoXNavigation, NavoXSourceList, NavoXStatus } from "./navox-ui";
import { NewsAnswerCard, referenceableTurnId } from "./news-chat";
import { NewsStoryCard } from "./news-workspace";
import { StoryIntelligence, StorySummaryView } from "./story-intelligence";

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

describe("News conversational and research UI", () => {
  const answer: NewsAnswer = {
    id: "turn-two",
    sequence: 2,
    question: "What changed?",
    status: "READY",
    message: "Here is what the sources report.",
    as_of: "2026-09-29T12:05:00Z",
    actions_executed: false,
    retrieval_limited: true,
    source_scope: "owned_permitted_items",
    freshness: "FRESH",
    facts: [
      {
        item_id: "item-one",
        text: "The source reported a measured observation.",
        source_name: "Science Source",
        source_url: "https://science.example/report",
        source_id: "source-one",
        status: "ATTRIBUTED",
      },
      {
        item_id: "item-two",
        text: "A second source reported the update independently.",
        source_name: "Second Source",
        source_url: "https://second.example/report",
        source_id: "source-two",
        status: "CORROBORATED",
      },
    ],
  };

  it("numbers current facts and exposes reference controls only when allowed", () => {
    const current = renderToStaticMarkup(
      createElement(NewsAnswerCard, {
        answer,
        canReference: true,
        onReference: vi.fn(),
      }),
    );
    expect(current).toContain("<ol");
    expect(current).toContain("Ask about #1");
    expect(current).toContain("Ask about #2");
    expect(current).toContain("Fresh sources");
    expect(current).toContain("bounded set of currently connected sources");
    expect(current).toContain('href="https://science.example/report"');

    const older = renderToStaticMarkup(
      createElement(NewsAnswerCard, {
        answer,
        canReference: false,
        onReference: vi.fn(),
      }),
    );
    expect(older).not.toContain("Ask about #1");
  });

  it("references only the immediately latest ready answer", () => {
    expect(referenceableTurnId([answer])).toBe(answer.id);
    for (const status of [
      "PROCESSING",
      "UNAVAILABLE",
      "SOURCES_CHANGED",
    ] as const) {
      expect(
        referenceableTurnId([answer, { ...answer, id: "latest", status }]),
      ).toBeUndefined();
      const html = renderToStaticMarkup(
        createElement(NewsAnswerCard, {
          answer: { ...answer, status },
          canReference: true,
          onReference: vi.fn(),
        }),
      );
      expect(html).not.toContain("Ask about #");
      expect(html).not.toContain("The source reported a measured observation.");
      expect(html).not.toContain("Sources checked");
    }
    expect(referenceableTurnId([])).toBeUndefined();
  });

  it("keeps uncertainty and attribution visible in a source-backed summary", () => {
    const summary: NewsStorySummary = {
      status: "READY",
      headline: "A measured observation",
      headline_source_url: "https://science.example/report",
      headline_attribution: "Science Source",
      headline_status: "ATTRIBUTED",
      as_of: "2026-09-29T12:05:00Z",
      actions_executed: false,
      sections: [
        {
          heading: "what_is_unclear",
          facts: [
            {
              claim_id: "claim-one",
              text: "The exact cause has not been established.",
              status: "UNCONFIRMED",
              attributed_to: "Science Source",
              source_name: "Science Source",
              source_url: "https://science.example/report",
            },
          ],
        },
      ],
    };
    const html = renderToStaticMarkup(
      createElement(StorySummaryView, { summary }),
    );
    expect(html).toContain("NavoX summary");
    expect(html).toContain("What is still unclear");
    expect(html).toContain("Not confirmed");
    expect(html).toContain("Science Source");
    expect(html).not.toContain("confidence");
  });

  it("hides unavailable deep-news controls while preserving recorded changes", () => {
    const basic = renderToStaticMarkup(
      createElement(StoryIntelligence, {
        story,
        availability: {
          feed: true,
          chat: true,
          x_trends: false,
          deep_research: false,
          coverage_comparison: false,
        },
      }),
    );
    expect(basic).toContain("What changed");
    expect(basic).not.toContain("Timeline");
    expect(basic).not.toContain("Compare sources");

    const deep = renderToStaticMarkup(
      createElement(StoryIntelligence, {
        story,
        availability: {
          feed: true,
          chat: true,
          x_trends: false,
          deep_research: true,
          coverage_comparison: true,
        },
      }),
    );
    expect(deep).toContain("Timeline");
    expect(deep).toContain("Compare sources");
    expect(deep).toContain("What changed");
  });
});
