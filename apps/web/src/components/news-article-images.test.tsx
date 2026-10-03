import type { NewsStory, NewsStorySummary } from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { newsPublisher, safeNewsImageUrl } from "../lib/news";
import { NewsStoryImage } from "./news-story-image";
import { NewsStoryCard, OriginalArticleLink } from "./news-workspace";
import { StorySummaryView } from "./story-intelligence";

const photo = {
  url: "https://photos.example.com/report.jpg?width=1200",
  alt: "An observation at the research site.",
  credit: "Photo: Fixture photographer / Science Source",
};
const story: NewsStory = {
  id: "story-one",
  headline: "A measured observation",
  description: null,
  image: photo,
  category: "science",
  verification_status: "UNCONFIRMED",
  lifecycle_status: "DISCOVERED",
  source_count: 1,
  published_at: "2026-10-02T12:00:00Z",
  event_started_at: null,
  last_updated_at: "2026-10-02T12:00:00Z",
  retrieved_at: "2026-10-02T12:00:00Z",
  version: 1,
  saved: false,
  followed: false,
  evidence_pending: true,
  sources: [
    {
      id: "item",
      source_id: "source",
      source_name: "Science Source via Perigon",
      headline: "A measured observation",
      canonical_url: "https://science.example.com/report",
      description: null,
      published_at: "2026-10-02T12:00:00Z",
      updated_at: null,
      event_started_at: null,
      retrieved_at: "2026-10-02T12:00:00Z",
      last_observed_at: "2026-10-02T12:00:00Z",
      expires_at: "2026-10-03T12:00:00Z",
      revision: 1,
    },
  ],
};

describe("Article photo and publisher navigation", () => {
  it("makes the photo and headline one story link and preserves credit and uncertainty", () => {
    const html = renderToStaticMarkup(
      createElement(NewsStoryCard, { story, featured: true, onSave: vi.fn() }),
    );
    expect(html).toContain(
      'src="https://photos.example.com/report.jpg?width=1200"',
    );
    expect(html).toContain(photo.alt);
    expect(html).toContain(photo.credit);
    expect(html).toContain('href="/news/stories/story-one"');
    expect(html).toContain("Explore story");
    expect(html).toContain("Not confirmed");
    expect(html).not.toContain("via Perigon");
    expect(html.match(/<a /g)).toHaveLength(1);
    expect(html.match(/<button/g)).toHaveLength(1);
    expect(html).toContain('referrerPolicy="no-referrer"');
    expect(html).not.toContain("/_next/image");
  });
  it("keeps metadata-only stories usable without fabricated or stock photos", () => {
    const html = renderToStaticMarkup(
      createElement(NewsStoryCard, {
        story: { ...story, image: null },
        onSave: vi.fn(),
      }),
    );
    expect(html).toContain(story.headline);
    expect(html).toContain("Explore story");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("placeholder");
  });
  it("opens the actual original article in a separate tab", () => {
    const html = renderToStaticMarkup(
      createElement(OriginalArticleLink, { story }),
    );
    expect(html).toContain('href="https://science.example.com/report"');
    expect(html).toContain("Read the full article at Science Source");
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).not.toContain("via Perigon");
    expect(newsPublisher("BBC via Perigon")).toBe("BBC");
    expect(newsPublisher("CNN")).toBe("CNN");
  });
  it("does not render a photo without supplied credit or description", () => {
    for (const image of [
      null,
      { ...photo, credit: "" },
      { ...photo, alt: "" },
    ]) {
      expect(
        renderToStaticMarkup(createElement(NewsStoryImage, { image })),
      ).toBe("");
    }
  });
  it.each([
    "javascript:alert(1)",
    "http://photos.example.com/a.jpg",
    "https://localhost/a.jpg",
    "https://127.0.0.1/a.jpg",
    "https://169.254.169.254/a.jpg",
    "https://[::1]/a.jpg",
    "https://pictures.internal/a.jpg",
    "https://photos.example.com:8443/a.jpg",
    "https://user:password@photos.example.com/a.jpg",
    "https://photos.example.com/a.jpg#secret",
    "https://photos.example.com/a.jpg?apiKey=secret",
    "https://photos.example.com/a.jpg?signature=secret",
    "https://photos.example.com/a.jpg?%2561uth=secret",
  ])("rejects unsafe or credential-bearing photo URL %s", (url) => {
    expect(safeNewsImageUrl(url)).toBeNull();
    expect(
      renderToStaticMarkup(
        createElement(NewsStoryImage, { image: { ...photo, url } }),
      ),
    ).toBe("");
  });
});

describe("Readable story summaries", () => {
  const summary: NewsStorySummary = {
    status: "READY",
    headline: null,
    headline_source_url: null,
    headline_attribution: null,
    headline_status: "ATTRIBUTED",
    as_of: "2026-10-02T12:00:00Z",
    actions_executed: false,
    sections: [
      "what_happened",
      "why_it_matters",
      "what_is_unclear",
      "latest_development",
    ].map((heading, index) => ({
      heading: heading as NewsStorySummary["sections"][number]["heading"],
      facts: [
        {
          claim_id: String(index),
          text: "A source-supported statement.",
          status: "ATTRIBUTED",
          attributed_to: null,
          source_name: "Science Source",
          source_url: "https://science.example.com/report",
        },
      ],
    })),
  };
  it("shows detail with citations and the four useful sections", () => {
    const html = renderToStaticMarkup(
      createElement(StorySummaryView, { summary }),
    );
    for (const label of [
      "NavoX summary",
      "What happened",
      "Why it matters",
      "What is still unclear",
      "Latest development",
    ])
      expect(html).toContain(label);
    expect(html).toContain('href="https://science.example.com/report"');
    expect(html).toContain("Source statement");
  });
  it.each(["UNAVAILABLE", "SOURCES_CHANGED", "PENDING"] as const)(
    "does not leak old facts when summary is %s",
    (status) => {
      const html = renderToStaticMarkup(
        createElement(StorySummaryView, { summary: { ...summary, status } }),
      );
      expect(html).toContain("NavoX summary");
      expect(html).not.toContain("A source-supported statement.");
      expect(html).not.toContain("What happened");
      if (status === "UNAVAILABLE")
        expect(html).toContain("read the full report");
    },
  );
});
