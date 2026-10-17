import { afterEach, describe, expect, it, vi } from "vitest";
import {
  lmsCourseMatchesComplete,
  lmsFeedAttention,
  lmsFeedRequest,
  lmsNeedsFreshLink,
  lmsSafeSourceUrl,
  readLmsCourseMatches,
  readLmsFeed,
  readLmsFeedMatches,
  readLmsFeeds,
  readLmsItems,
  readLmsPreview,
  readLmsRefresh,
} from "./student-lms-feeds";

const feedId = "11111111-1111-4111-8111-111111111111",
  courseId = "22222222-2222-4222-8222-222222222222",
  itemId = "33333333-3333-4333-8333-333333333333",
  token = "44444444-4444-4444-8444-444444444444";
const privateUrl =
  "https://school.example/calendar/private-SYNTHETIC-KEY.ics?auth=SYNTHETIC-SECRET";
const feed = {
  id: feedId,
  name: "My deadlines",
  domain: "school.example",
  platform: "canvas",
  timezone: "America/New_York",
  all_day_due_time: "23:59",
  state: "active",
  revision: 1,
  last_synced_at: "2026-10-19T12:00:00Z",
  next_sync_at: "2026-10-19T14:00:00Z",
  last_error: null,
  course_matches: { "CS 100": courseId },
};
const item = {
  external_id: "event-id",
  occurrence_id: "2026-10-20",
  title: "Lab report",
  kind: "assignment",
  starts_at: null,
  due_at: "2026-10-21T03:59:00Z",
  ends_at: null,
  all_day: true,
  course_hint: "CS 100",
  details: "Review the rubric.",
  source_url: "https://school.example/courses/100/assignments/1",
  location: null,
  source_updated_at: null,
  cancelled: false,
};
const preview = {
  preview_token: token,
  feed_id: feedId,
  preview_hash: "a".repeat(64),
  expires_at: "2026-10-19T12:30:00Z",
  domain: feed.domain,
  platform: feed.platform,
  count: 1,
  course_hints: ["CS 100"],
  all_day_count: 1,
  all_day_due_time: "23:59",
  items: [item],
  notice: "Review before connecting.",
};
afterEach(() => vi.restoreAllMocks());
describe("calendar response privacy and review", () => {
  it("retains domain-only connection metadata and explicit source clock choices", () => {
    expect(readLmsFeed(feed)).toEqual(feed);
    expect(readLmsPreview(preview)).toMatchObject({
      domain: "school.example",
      all_day_due_time: "23:59",
      course_hints: ["CS 100"],
    });
  });
  it("refuses a raw private link masquerading as the displayed domain", () => {
    expect(() => readLmsFeed({ ...feed, domain: privateUrl })).toThrow(
      "saved details could not be checked",
    );
  });
  it("keeps cancellation, disagreement and source revision distinct for assignment review", () => {
    const result = readLmsItems({
      items: [
        {
          id: itemId,
          revision: 3,
          course_id: courseId,
          cancelled: false,
          conflicted: true,
          item,
        },
      ],
      next_cursor: null,
    });
    expect(result.items[0]).toMatchObject({
      revision: 3,
      cancelled: false,
      conflicted: true,
      course_id: courseId,
    });
  });
  it("requires every discovered course hint to be explicitly matched or kept unassigned", () => {
    expect(
      lmsCourseMatchesComplete(["CS 100", "MATH"], { "CS 100": courseId }),
    ).toBe(false);
    expect(
      lmsCourseMatchesComplete(["CS 100", "MATH"], {
        "CS 100": courseId,
        MATH: null,
      }),
    ).toBe(true);
    expect(
      lmsCourseMatchesComplete(["CS 100"], { "CS 100": courseId }, []),
    ).toBe(false);
    expect(
      lmsCourseMatchesComplete(["CS 100"], { "CS 100": courseId }, [courseId]),
    ).toBe(true);
  });
  it("handles unusual course labels as own properties without changing object prototypes", () => {
    const value = readLmsCourseMatches(
      Object.fromEntries([["__proto__", courseId]]),
    );
    expect(Object.hasOwn(value, "__proto__")).toBe(true);
    expect(Object.getPrototypeOf(value)).toBe(Object.prototype);
    expect(lmsCourseMatchesComplete(["__proto__"], value, [courseId])).toBe(
      true,
    );
  });
  it("reads current match revisions including newly unconfirmed hints", () => {
    expect(
      readLmsFeedMatches({
        revision: 2,
        course_hints: ["CS 100", "NEW"],
        course_matches: { "CS 100": courseId },
        unconfirmed_hints: ["NEW"],
      }),
    ).toMatchObject({ revision: 2, unconfirmed_hints: ["NEW"] });
  });
  it("does not confuse a requested update with a completed sync", () => {
    expect(readLmsRefresh({ state: "queued", feed_id: feedId })).toEqual({
      state: "queued",
      feed_id: feedId,
    });
    expect(() =>
      readLmsRefresh({ state: "synced", feed_id: feedId }),
    ).toThrow();
  });
  it.each([
    "javascript:alert(1)",
    "http://school.example/item",
    "https://user:pass@school.example/item",
  ])("does not render an unsafe source destination", (url) => {
    expect(lmsSafeSourceUrl(url)).toBeNull();
  });
  it("permits an HTTPS assignment source without exposing the private feed link", () => {
    expect(lmsSafeSourceUrl(item.source_url)).toBe(item.source_url);
  });
});
describe("private calendar requests", () => {
  it("uses read-only no-store list requests without synchronizing a source", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json({ feeds: [feed] }));
    await lmsFeedRequest("", readLmsFeeds);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringMatching(/\/student\/lms-feeds$/),
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("sends a private link only in an explicit preview body", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(preview));
    await lmsFeedRequest("/preview", readLmsPreview, "POST", {
      url: privateUrl,
      platform: "canvas",
      timezone: feed.timezone,
      all_day_due_time: "23:59",
    });
    expect(fetch.mock.calls[0][0]).not.toContain("SYNTHETIC-KEY");
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject({
      url: privateUrl,
    });
  });
  it.each([400, 403, 404, 409, 422, 429, 500, 503])(
    "never repeats server error details that could contain a private URL (%s)",
    async (status) => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        Response.json({ detail: privateUrl }, { status }),
      );
      try {
        await lmsFeedRequest("/preview", readLmsPreview, "POST", {
          url: privateUrl,
        });
        expect.fail("request should fail");
      } catch (cause) {
        expect((cause as Error).message).not.toContain("SYNTHETIC");
        expect((cause as Error).message).not.toContain("https://");
        expect(cause).toMatchObject({ uncertain: status >= 500 });
      }
    },
  );
  it("does not echo a transport exception containing credentials", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error(privateUrl));
    await expect(
      lmsFeedRequest("/preview", readLmsPreview, "POST", { url: privateUrl }),
    ).rejects.toMatchObject({
      uncertain: true,
      message:
        "The calendar could not be reached. Reload saved connections before trying again.",
    });
  });
  it("treats malformed confirmation success as unknown until a saved-list reload", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ id: feedId }),
    );
    await expect(
      lmsFeedRequest("", readLmsFeed, "POST", {}),
    ).rejects.toMatchObject({ uncertain: true });
  });
});

describe("actionable feed errors without secret disclosure", () => {
  it.each(["feed_link_expired", "feed_history_limit"])(
    "routes %s to a fresh reviewed link",
    (code) => {
      expect(lmsNeedsFreshLink(code)).toBe(true);
      expect(lmsFeedAttention(code)).toContain("fresh");
      expect(lmsFeedAttention(code)).not.toContain("https://");
    },
  );
  it("does not display an arbitrary backend error code", () => {
    expect(lmsFeedAttention(privateUrl)).not.toContain("SYNTHETIC");
  });
  it("shows an exact allowlisted unsupported-calendar message", async () => {
    const safe =
      "This calendar uses an unsupported or overly complex pattern. No events were saved. Try an exported schedule or another feed.";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ detail: safe }, { status: 422 }),
    );
    await expect(
      lmsFeedRequest("/preview", readLmsPreview, "POST", {}),
    ).rejects.toMatchObject({ message: safe, uncertain: false });
  });
});
