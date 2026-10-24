import {
  executeStudentTool,
  type StudentToolUpstream,
} from "@navox/assistant-runtime/student-tools";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  readStudentAssistantReply,
  type StudentAssistantReply,
} from "../lib/student-agent-client";
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
  it.each([false, true])(
    "matches the examples and introduction to tool-context availability (%s)",
    (toolContextConsentAvailable) => {
      const html = renderToStaticMarkup(
        createElement(StudentAssistantPanel, {
          accountId: "workspace:user",
          toolContextConsentAvailable,
        }),
      );
      for (const example of [
        "What class do I have next?",
        "List my tasks",
        "Preview my daily plan",
        "Explain a flip-flop",
      ]) {
        expect(html).toContain(example);
      }
      expect(html.includes("Find “flip-flop” in my course materials")).toBe(
        toolContextConsentAvailable,
      );
      expect(html.includes("find a passage in your course materials")).toBe(
        toolContextConsentAvailable,
      );
      expect(html.includes('type="checkbox"')).toBe(
        toolContextConsentAvailable,
      );
      if (!toolContextConsentAvailable) {
        expect(html).toContain("preview a daily plan");
        expect(html).not.toContain("course materials");
      }
    },
  );
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

describe("verified student facts reach the answer display", () => {
  const courseId = "55555555-5555-4555-8555-555555555555";
  const turnId = "44444444-4444-4444-8444-444444444444";
  const sessionId = "33333333-3333-4333-8333-333333333333";
  const scope = {
    user_id: "11111111-1111-4111-8111-111111111111",
    workspace_id: "22222222-2222-4222-8222-222222222222",
    personal: true,
    paused: false,
  };
  async function display(
    name: "course.info" | "plan.preview",
    source: unknown,
  ) {
    const unavailable = vi.fn(async () => {
      throw new Error(
        "No model or unrelated tool is used to present saved facts",
      );
    });
    const upstream: StudentToolUpstream = {
      authorize: async () => scope,
      courses: async () => source,
      preview: async () => source,
      clock: unavailable,
      lookup: unavailable,
      tasks: unavailable,
      feeds: unavailable,
      select: unavailable,
      converse: unavailable,
    };
    const tool = await executeStudentTool(
      upstream,
      scope,
      { call_id: turnId, name, args: {} },
      new AbortController().signal,
    );
    const envelope = readStudentAssistantReply(
      {
        turn_id: turnId,
        session_id: sessionId,
        receipt_id: null,
        reply: {
          status: tool.effect === "review" ? "review" : "answer",
          source: "deterministic",
          answer: tool.summary,
          observations: [tool],
          actions_executed: false,
          transcript_saved: false,
        },
      },
      turnId,
      sessionId,
    );
    expect(unavailable).not.toHaveBeenCalled();
    return {
      envelope,
      html: renderToStaticMarkup(
        createElement(StudentAssistantAnswer, { reply: envelope.reply }),
      ),
    };
  }

  it.each([null, "Dr Mira Vale"])(
    "renders the saved course term and professor state (%s) with its Classes link",
    async (professor) => {
      const { html, envelope } = await display("course.info", {
        courses: [
          {
            id: courseId,
            code: "PHYS 121",
            name: "Mechanics",
            professor,
            term_start: "2026-09-01",
            term_end: "2026-12-19",
          },
        ],
      });
      expect(html).toContain("PHYS 121 — Mechanics");
      expect(html).toContain(`Professor: ${professor ?? "not recorded"}`);
      expect(html).toContain("Sep 1, 2026 to Dec 19, 2026");
      expect(html).toContain('href="/classes"');
      expect(envelope.reply).not.toHaveProperty("observations");
    },
  );

  it("renders concrete review-only plan details and a fixed link without raw receipt data", async () => {
    const { html, envelope } = await display("plan.preview", {
      preview_id: courseId,
      preview_hash: "a".repeat(64),
      expires_at: "2026-11-04T15:00:00Z",
      base_revision: 1,
      requires_acceptance: true, replaced_blocks: [],
      result: {
        planning_only: true,
        start_at: "2026-11-04T00:00:00Z",
        end_at: "2026-11-05T00:00:00Z",
        blocks: [
          {
            task_id: courseId,
            title: "Derivative problem sheet",
            start_at: "2026-11-04T14:00:00Z",
            end_at: "2026-11-04T14:45:00Z",
            minutes: 45,
            focus_window: true,
          },
        ],
        unfitted_work: [
          {
            task_id: turnId,
            title: "Mechanics problem set",
            minutes: 120,
            reason: "cannot_meet_deadline",
            due_at: null,
          },
        ],
        capacities: [
          {
            day: "2026-11-04",
            free_minutes: 60,
            planned_minutes: 45,
            buffer_minutes: 9,
          },
        ],
        warnings: ["Review the saved deadline."],
      },
    });
    expect(html).toContain("Derivative problem sheet: 45 minutes");
    expect(html).toContain("2026-11-04 14:00–14:45 UTC");
    expect(html).toContain("in a focus window");
    expect(html).toContain("Mechanics problem set: 120 minutes");
    expect(html).toContain("the available time cannot meet the deadline");
    expect(html).toContain("60 free minutes, 45 planned, 9 buffer minutes");
    expect(html).toContain("Review the saved deadline.");
    expect(html).toContain("before saving");
    expect(html).toContain('href="/planner"');
    expect(JSON.stringify(envelope)).not.toContain("preview_hash");
    expect(JSON.stringify(envelope)).not.toContain("observations");
  });
});
