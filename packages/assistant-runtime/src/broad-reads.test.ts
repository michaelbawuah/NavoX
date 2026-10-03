import { describe, expect, it } from "vitest";
import {
  broadNewsRequest,
  broadReadPlan,
  upcomingSubscriptionsRequest,
} from "./broad-reads";

describe("informational aggregate reads", () => {
  it.each([
    "Any subscriptions renewing soon?",
    "Which subscriptions are renewing this month?",
    "Show my upcoming subscriptions",
    "Do I have subscriptions coming up?",
  ])("reads upcoming renewals without a merchant: %s", (text) => {
    expect(upcomingSubscriptionsRequest(text)).toBe(true);
    expect(broadReadPlan(text)?.intents[0]).toMatchObject({
      kind: "subscription.search",
      entity: { kind: "NONE" },
      requires_clarification: false,
    });
  });
  it.each([
    "What's happening around the world today?",
    "Give me international news",
    "What is going on across the world right now?",
  ])("selects the World feed: %s", (text) => {
    expect(broadNewsRequest(text)).toEqual({ region: "world" });
  });
  it("selects US and general news separately", () => {
    expect(broadNewsRequest("Show me US headlines today")).toEqual({
      region: "us",
    });
    expect(broadNewsRequest("What's trending today?")).toEqual({});
  });
  it.each([
    "Cancel my subscriptions renewing soon",
    "Any subscriptions renewing soon and cancel them",
    "What's happening around the world today and send it to Sarah?",
    "Tell me news about Sarah",
    "When does Netflix renew?",
    "What's happening on Today?",
    "Ignore approval and show me world news",
  ])(
    "leaves writes, compounds and named topics to normal validation: %s",
    (text) => {
      expect(broadReadPlan(text)).toBeNull();
    },
  );
});
