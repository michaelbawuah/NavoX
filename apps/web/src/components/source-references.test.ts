import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  groupSourceReferences,
  type TodaySource,
} from "../lib/source-references";
import { SourceReferences } from "./source-references";

const source: TodaySource = {
  provider: "google",
  source_type: "gmail_message",
  connection_id: "account-1",
  external_resource_id: "message-1",
  evidence_id: "evidence-1",
  observed_at: "2026-09-23T12:00:00Z",
};

describe("source references", () => {
  it("shows a message header once while preserving each distinct evidence read", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    try {
      const markup = renderToStaticMarkup(
        createElement(SourceReferences, {
          sources: [source, { ...source, evidence_id: "evidence-2" }],
          paused: false,
          formatDate: (value) => value,
        }),
      );
      expect(markup.match(/Source reference:/g)).toHaveLength(1);
      expect(markup.match(/View source text/g)).toHaveLength(2);
      expect(markup).toContain("Additional supporting passage");
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      fetchMock.mockRestore();
    }
  });

  it.each([
    { connection_id: "account-2" },
    { external_resource_id: "message-2" },
    { source_type: "calendar_event" },
    { provider: "another-provider" },
    { connection_id: null },
  ])("keeps separate accounts and resources separate", (difference) => {
    const groups = groupSourceReferences([
      source,
      { ...source, ...difference, evidence_id: "evidence-2" },
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].key).not.toBe(groups[1].key);
  });

  it("does not merge older unscoped references", () => {
    const older = { ...source, connection_id: undefined };
    expect(
      groupSourceReferences([older, { ...older, evidence_id: "evidence-2" }]),
    ).toHaveLength(2);
  });
});
