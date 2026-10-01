import type { AnswerCitation } from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AskCitations, KnowledgeAsk } from "./knowledge-ask";

const citation: AnswerCitation = {
  resource_id: "3d2b6b1a-0000-4000-8000-000000000001",
  source_type: "EMAIL",
  title: "Quarterly planning review",
  canonical_url: "https://mail.example.com/message-1",
  source_version: "etag-1",
  source_updated_at: "2026-09-28T09:00:00Z",
  excerpt_index: 0,
  excerpt_text: "The quarterly planning review covers the budget forecast.",
  fact_id: null,
  fact_label: null,
  fact_value: null,
  authority: "Source system",
  sensitivity: "PERSONAL",
  origin: "CONNECTED",
};

describe("grounded ask citations", () => {
  it("renders the exact stored span with a safe source link", () => {
    const markup = renderToStaticMarkup(
      createElement(AskCitations, { citations: [citation] }),
    );
    expect(markup).toContain(
      "The quarterly planning review covers the budget forecast.",
    );
    expect(markup).toContain("Quoted passage 1");
    expect(markup).toContain("Quarterly planning review");
    expect(markup).toContain('href="https://mail.example.com/message-1"');
    expect(markup).toContain('target="_blank"');
    expect(markup).toContain('rel="noopener noreferrer"');
    expect(markup).not.toMatch(/score|similarity|relevance/i);
  });

  it("renders a structured fact with its own authority label", () => {
    const markup = renderToStaticMarkup(
      createElement(AskCitations, {
        citations: [
          {
            ...citation,
            excerpt_index: null,
            excerpt_text: null,
            fact_id: "calendar.event:id:at",
            fact_label: "Event start",
            fact_value: "2026-10-02T15:00:00+00:00",
            source_type: "CALENDAR_EVENT",
            authority: "Calendar (source system)",
            canonical_url: null,
          },
        ],
      }),
    );
    expect(markup).toContain("Event start");
    expect(markup).toContain("2026-10-02T15:00:00+00:00");
    expect(markup).toContain("No source link available");
  });
});

describe("grounded ask workspace", () => {
  it("offers a question form, numbered follow-ups and session controls", () => {
    const markup = renderToStaticMarkup(createElement(KnowledgeAsk, {}));
    expect(markup).toContain("Ask about your connected sources");
    expect(markup).toContain("Your sessions");
    expect(markup).toContain("Loading sessions…");
    expect(markup).not.toMatch(/score|similarity|relevance/i);
    expect(markup).not.toContain("sources you cannot open");
  });
});
