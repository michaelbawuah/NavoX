import type { NewsStory } from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  acknowledgeableVersion,
  type FollowedUpdate,
  type FollowingPage,
} from "../lib/news-following";
import {
  FollowedStoryUpdates,
  FollowedUpdateList,
} from "./followed-story-updates";

const story: NewsStory = {
  id: "story-one",
  headline: "A source reported an observation",
  description: null,
  category: "science",
  verification_status: "UNCONFIRMED",
  lifecycle_status: "DISCOVERED",
  source_count: 1,
  published_at: "2026-09-29T12:00:00Z",
  event_started_at: null,
  last_updated_at: "2026-09-29T12:00:00Z",
  retrieved_at: "2026-09-29T12:00:00Z",
  version: 3,
  sources: [],
  saved: false,
  followed: true,
  evidence_pending: true,
};
const entry: FollowedUpdate = {
  story,
  since_version: 1,
  through_version: 2,
  history_complete: false,
  baseline_required: false,
  events: [
    {
      version: 2,
      generated_at: "2026-09-29T12:00:00Z",
      description: "Evidence changed",
    },
  ],
};
const page: FollowingPage = {
  entries: [entry],
  next_story_id: null,
  as_of: "2026-09-29T12:00:00Z",
  scope: "followed_stories",
  external_notifications_sent: false,
};

function render(value: FollowingPage, busy = false) {
  return renderToStaticMarkup(
    createElement(FollowedUpdateList, { page: value, busy, onSeen: vi.fn() }),
  );
}

describe("Explicit followed-story updates", () => {
  it("marks only the displayed version, not a newer story version", () => {
    expect(acknowledgeableVersion(entry)).toBe(2);
    expect(acknowledgeableVersion({ ...entry, through_version: 4 })).toBeNull();
    expect(acknowledgeableVersion({ ...entry, through_version: 1 })).toBeNull();
    expect(
      acknowledgeableVersion({ ...entry, through_version: 1.5 }),
    ).toBeNull();
  });
  it("preserves uncertainty and discloses incomplete history", () => {
    const html = render(page);
    expect(html).toContain("Not confirmed");
    expect(html).toContain("partial history");
    expect(html).toContain("Mark shown updates seen");
    expect(html).not.toContain("Verified");
  });
  it("does not offer to acknowledge a missing historical interval", () => {
    const html = render({
      ...page,
      entries: [{ ...entry, events: [], through_version: 1 }],
    });
    expect(html).toContain("partial history");
    expect(html).not.toContain("<button");
  });
  it("treats older follows as an explicit new starting point", () => {
    const legacy = {
      ...entry,
      events: [],
      since_version: null,
      baseline_required: true,
    };
    expect(acknowledgeableVersion(legacy)).toBe(2);
    expect(render({ ...page, entries: [legacy] })).toContain(
      "Start tracking from this version",
    );
  });
  it("does not claim that a quiet bounded page is the complete inbox", () => {
    const html = render({
      ...page,
      entries: [],
      next_story_id: "more-stories",
    });
    expect(html).toContain("more followed stories to check");
    expect(html).not.toContain("caught up");
  });
  it("escapes source text and disables acknowledgement while a request runs", () => {
    const html = render(
      {
        ...page,
        entries: [
          {
            ...entry,
            story: { ...story, headline: "<script>doBadThings()</script>" },
          },
        ],
      },
      true,
    );
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("disabled");
  });
  it("starts without fetching or recording a read", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    try {
      const html = renderToStaticMarkup(createElement(FollowedStoryUpdates));
      expect(html).toContain("Check followed stories");
      expect(html).toContain("does not mark anything as read");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("Follow-update refresh boundary", () => {
  it("removes the stateful results subtree while parent news is refreshing", () => {
    const before = FollowedStoryUpdates({ refreshing: false });
    const refreshing = FollowedStoryUpdates({ refreshing: true });
    const after = FollowedStoryUpdates({ refreshing: false });
    expect(refreshing.type).toBe("p");
    expect(refreshing.type).not.toBe(before.type);
    expect(after.type).toBe(before.type);
    expect(renderToStaticMarkup(refreshing)).toContain("Refreshing news");
    expect(renderToStaticMarkup(refreshing)).not.toContain(
      "Mark shown updates seen",
    );
  });
  it("does not fetch or acknowledge while its parent reloads the catalog", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    try {
      const html = renderToStaticMarkup(
        FollowedStoryUpdates({ refreshing: true }),
      );
      expect(html).toContain('role="status"');
      expect(html).not.toContain("<button");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
