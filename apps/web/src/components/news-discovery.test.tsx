import type { NewsStory } from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { loadNewsFeed, NewsRequestError, newsFeedPath } from "../lib/news";
import { NewsFeedTabs } from "./news-workspace";
import { RelatedStoryList } from "./related-stories";

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

async function trendingWith(response: Response) {
  const fetch = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetch);
  try {
    const error = await loadNewsFeed("trending").catch((reason) => reason);
    return { fetch, error };
  } finally {
    vi.unstubAllGlobals();
  }
}

describe("News feed selection", () => {
  it("requests the trending feed and returns its stories", async () => {
    const requested: unknown[] = [];
    const fetch = vi.fn(async (input: unknown) => {
      requested.push(input);
      return new Response(JSON.stringify([story]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetch);
    try {
      expect(await loadNewsFeed("trending")).toEqual([story]);
      expect(String(requested[0])).toMatch(/\/news\/trending$/);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("surfaces an empty trending feed without inventing stories", async () => {
    const fetch = vi.fn(
      async () =>
        new Response("[]", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetch);
    try {
      expect(await loadNewsFeed("trending")).toEqual([]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("reports trending failures and signed-out reads", async () => {
    const unavailable = await trendingWith(
      new Response("nope", { status: 503 }),
    );
    expect(unavailable.error).toBeInstanceOf(NewsRequestError);
    expect((unavailable.error as NewsRequestError).status).toBe(503);
    expect((unavailable.error as Error).message).toBe(
      "We couldn’t refresh your news. Please try again.",
    );

    const signedOut = await trendingWith(new Response("no", { status: 401 }));
    expect((signedOut.error as NewsRequestError).status).toBe(401);
    expect((signedOut.error as Error).message).toBe(
      "Sign in to see your news.",
    );
  });

  it("keeps every feed reachable and states the trending limits in words", () => {
    const tabs = [
      ["top", "/top"],
      ["for-you", "/for-you"],
      ["trending", "/trending"],
      ["saved", "/saved"],
      ["science", "/categories/science"],
    ] as const;
    for (const [feed, path] of tabs) expect(newsFeedPath(feed)).toBe(path);

    const basic = renderToStaticMarkup(
      createElement(NewsFeedTabs, { feed: "top" as const, onSelect: vi.fn() }),
    );
    expect(basic).toContain("Trending");
    expect(basic).not.toContain("global popularity");

    const trending = renderToStaticMarkup(
      createElement(NewsFeedTabs, {
        feed: "trending" as const,
        onSelect: vi.fn(),
      }),
    );
    expect(trending).toContain("Trending");
    expect(trending).toContain(
      "Stories drawing attention in your connected news sources",
    );
    expect(trending).toContain(
      "does not measure worldwide popularity or importance, and does not confirm a report",
    );
    expect(trending).toContain('aria-pressed="true"');
  });
});

describe("Related stories", () => {
  it("renders nothing at all when no story shares a claim", () => {
    const html = renderToStaticMarkup(
      createElement(RelatedStoryList, { stories: [] }),
    );
    expect(html).toBe("");
    expect(html).not.toContain("Related stories");
  });

  it("links related stories with their verification status", () => {
    const html = renderToStaticMarkup(
      createElement(RelatedStoryList, { stories: [story] }),
    );
    expect(html).toContain("Related stories");
    expect(html).toContain('href="/news/stories/story-one"');
    expect(html).toContain("A measured observation");
    expect(html).toContain("Not confirmed");
    expect(html).not.toContain("confidence");
  });
});
