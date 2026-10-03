import type { AssistantBlock } from "@navox/contracts";
import { describe, expect, it } from "vitest";
import {
  navigationHref,
  navigationTargets,
  parseClassNavigationTarget,
  parseResourceNavigation,
  parseSafeNavigationUrl,
} from "./navigation";

const EMAIL = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const THREAD = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const CONNECTION = "88888888-8888-4888-8888-888888888888";
const CLASS = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";

function emailItem(id: string, type: "EMAIL" | "EMAIL_THREAD"): AssistantBlock {
  return {
    kind: "ITEM",
    item: {
      id,
      type,
      title: "Renewal",
      description: null,
      status: "CURRENT",
      due_at: null,
      band: "CONNECTED",
      sources: [
        {
          provider: "navox",
          source_type: type,
          external_resource_id: "mail-1",
          evidence_id: id,
          connection_id: CONNECTION,
          observed_at: "2026-09-29T12:00:00.000Z",
        },
      ],
    },
  };
}

describe("guarded navigation targets", () => {
  it("names a cited email item as a same-origin guarded path", () => {
    const targets = navigationTargets([emailItem(EMAIL, "EMAIL")]);
    expect(targets).toHaveLength(1);
    expect(targets[0]).toMatchObject({
      item_id: EMAIL,
      source_type: "EMAIL",
      label: "Open that email",
      connection_id: CONNECTION,
    });
    const href = navigationHref("session-1", "turn-1", EMAIL);
    expect(href.startsWith("/api/v1/assistant/navigation?")).toBe(true);
    expect(href).toContain(`session_id=session-1`);
    expect(href).toContain(`item_id=${EMAIL}`);
  });

  it("names a class-navigation block and ignores uncited items", () => {
    const uncited: AssistantBlock = {
      kind: "ITEM",
      item: {
        id: EMAIL,
        type: "EMAIL",
        title: "Uncited",
        description: null,
        status: "CURRENT",
        due_at: null,
        band: "CONNECTED",
        sources: [],
      },
    };
    const classBlock: AssistantBlock = {
      kind: "CLASS_NAVIGATION",
      label: "Open Calendar",
      connection_id: CONNECTION,
      resource_id: CLASS,
    };
    const targets = navigationTargets([uncited, classBlock]);
    expect(targets).toEqual([
      {
        item_id: CLASS,
        source_type: "CLASS_MEETING",
        label: "Open Calendar",
        connection_id: CONNECTION,
        citations: [],
      },
    ]);
    // Canvas is deferred: an "Open Class" block never becomes a target.
    expect(
      navigationTargets([
        {
          kind: "CLASS_NAVIGATION",
          label: "Open Class",
          connection_id: CONNECTION,
          resource_id: CLASS,
        },
      ]),
    ).toEqual([]);
  });

  it("only accepts a verified https destination on the expected host", () => {
    expect(parseSafeNavigationUrl("https://mail.google.com/x", "EMAIL")).toBe(
      "https://mail.google.com/x",
    );
    expect(
      parseSafeNavigationUrl("https://mail.google.com/x", "EMAIL_THREAD"),
    ).toBe("https://mail.google.com/x");
    expect(
      parseSafeNavigationUrl(
        "https://calendar.google.com/calendar/event?eid=2",
        "CLASS_MEETING",
      ),
    ).toBe("https://calendar.google.com/calendar/event?eid=2");
    expect(
      parseSafeNavigationUrl(
        "https://www.google.com/calendar/u/0/r/week",
        "CLASS_MEETING",
      ),
    ).toBe("https://www.google.com/calendar/u/0/r/week");
    for (const value of [
      "javascript:alert(1)",
      "http://mail.google.com/x",
      "https://user:pass@mail.google.com/x",
      "not a url",
      "",
      null,
      42,
      `https://mail.google.com/${"a".repeat(3000)}`,
    ]) {
      expect(() => parseSafeNavigationUrl(value, "EMAIL")).toThrow(
        /could not be verified/i,
      );
    }
    // A foreign host never becomes a redirect, even over https.
    for (const [value, sourceType] of [
      ["https://evil.example/x", "EMAIL"],
      ["https://evil.example/x", "EMAIL_THREAD"],
      ["https://evil.example/calendar/event", "CLASS_MEETING"],
      ["https://canvas.instructure.com/calendar", "CLASS_MEETING"],
      ["https://mail.google.com/x", "CLASS_MEETING"],
      ["https://calendar.google.com/calendar/event", "EMAIL"],
    ] as const) {
      expect(() => parseSafeNavigationUrl(value, sourceType)).toThrow(
        /could not be verified/i,
      );
    }
  });

  it("rejects a mismatched resource or a missing canonical location", () => {
    const detail = {
      resource_id: THREAD,
      source_type: "EMAIL_THREAD",
      canonical_url: "https://mail.google.com/thread/1",
    };
    expect(
      parseResourceNavigation(detail, {
        resource_id: THREAD,
        source_type: "EMAIL_THREAD",
      }),
    ).toEqual({ url: "https://mail.google.com/thread/1" });
    expect(() =>
      parseResourceNavigation(detail, {
        resource_id: EMAIL,
        source_type: "EMAIL",
      }),
    ).toThrow(/could not be verified/i);
    expect(() =>
      parseResourceNavigation(
        { ...detail, canonical_url: null },
        { resource_id: THREAD, source_type: "EMAIL_THREAD" },
      ),
    ).toThrow(/could not be verified/i);
  });

  it("requires the exact class-navigation url envelope", () => {
    expect(
      parseClassNavigationTarget({ url: "https://calendar.google.com/event" }),
    ).toEqual({ url: "https://calendar.google.com/event" });
    for (const payload of [
      {},
      { url: "http://calendar.google.com/event" },
      { url: 7 },
      { not: "a url" },
      null,
    ]) {
      expect(() => parseClassNavigationTarget(payload)).toThrow(
        /could not be verified/i,
      );
    }
  });
});
