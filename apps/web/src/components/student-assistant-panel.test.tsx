import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { StudentAssistantReply } from "../lib/student-agent-client";
import {
  StudentAssistantAnswer,
  StudentAssistantPanel,
} from "./student-assistant-panel";

describe("student assistant text panel", () => {
  it("starts private, with no network request, history, or premature tool consent", () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    try {
      const html = renderToStaticMarkup(
        createElement(StudentAssistantPanel, { accountId: "workspace:user" }),
      );
      expect(html).toContain("What would help right now?");
      expect(html).toContain("does not save a conversation");
      expect(html).toContain("without chat history");
      expect(html).toContain('maxLength="500"');
      expect(html).not.toContain('type="checkbox"');
      expect(html).not.toContain("streaming");
      expect(fetcher).not.toHaveBeenCalled();
    } finally {
      fetcher.mockRestore();
    }
  });
  it("shows a separately available, unchecked and specific tool-context choice", () => {
    const html = renderToStaticMarkup(
      createElement(StudentAssistantPanel, {
        accountId: "workspace:user",
        toolContextConsentAvailable: true,
      }),
    );
    expect(html).toContain('type="checkbox"');
    expect(html).not.toContain(" checked=");
    expect(html).toContain("selected class, course, task and feed facts");
    expect(html).toContain("aren’t included in AI context");
    expect(html).toContain(
      "Matching passages from course materials are shown only to you",
    );
  });
  it("offers review links without rendering tool data or implying a saved plan", () => {
    const reply: StudentAssistantReply = {
      status: "review",
      source: "tools",
      answer: "Your plan is ready to review. Nothing was saved.",
      links: [
        {
          href: "/planner",
          label: "Open Planner",
          note: "Review every block before saving.",
        },
      ],
    };
    const html = renderToStaticMarkup(
      createElement(StudentAssistantAnswer, { reply }),
    );
    expect(html).toContain('href="/planner"');
    expect(html).toContain("before saving");
    expect(html).not.toContain("preview_hash");
    expect(html).not.toContain("<pre");
  });
  it("escapes provider text and explains how to ask a complete clarification", () => {
    const reply: StudentAssistantReply = {
      status: "clarify",
      source: "none",
      answer: "Which course? <script>alert(1)</script>",
      links: [],
    };
    const html = renderToStaticMarkup(
      createElement(StudentAssistantAnswer, { reply }),
    );
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("full question below");
  });
});
