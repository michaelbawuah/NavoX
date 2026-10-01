import type { AssistantTurnView } from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  AssistantBlockView,
  AssistantTurnViewBlock,
  assistantVoiceLabel,
  NavoXAssistant,
} from "./navox-assistant";

const turn: AssistantTurnView = {
  id: "88888888-8888-4888-8888-888888888888",
  sequence: 1,
  modality: "VOICE",
  state: "READY",
  question: "What am I missing today?",
  plan: null,
  decision: null,
  presentation: {
    presentation: "VOICE",
    speak: true,
    speech_text: "1 item needs attention now.",
    blocks: [
      { kind: "ANSWER", text: "1 item needs attention now." },
      {
        kind: "ITEM",
        item: {
          id: "task-1",
          type: "commitment",
          title: "Send the vendor recap",
          description: "Waiting on finance",
          status: "open",
          due_at: "2026-10-01T15:00:00+00:00",
          band: "attention",
          sources: [
            {
              provider: "google",
              source_type: "EMAIL",
              external_resource_id: "message-1",
              evidence_id: "evidence-1",
              connection_id: "connection-1",
              observed_at: "2026-09-29T09:00:00+00:00",
            },
          ],
        },
      },
      { kind: "DETAILS", lines: ["Attention band: 0.81"] },
    ],
  },
  action_refs: [],
  created_at: "2026-09-30T12:00:00.000Z",
};

describe("assistant transcript rendering", () => {
  it("renders class navigation through the same-origin authorization route", () => {
    const markup = renderToStaticMarkup(
      createElement(AssistantBlockView, {
        block: {
          kind: "CLASS_NAVIGATION",
          label: "Open Calendar",
          connection_id: "00000000-0000-4000-8000-000000000010",
          resource_id: "00000000-0000-4000-8000-000000000011",
        },
      }),
    );
    expect(markup).toContain("Open Calendar");
    expect(markup).toContain(
      "/api/v1/assistant/class-navigation?connection_id=",
    );
    expect(markup).toContain('rel="noopener noreferrer"');
    expect(markup).not.toContain("calendar.google.com");
  });

  it("renders the question, answer, item and citation selectors", () => {
    const markup = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, { turn }),
    );
    expect(markup).toContain("What am I missing today?");
    expect(markup).toContain("1 item needs attention now.");
    expect(markup).toContain("Send the vendor recap");
    expect(markup).toContain("google · EMAIL · message-1");
    expect(markup).toContain("Attention band: 0.81");
    expect(markup).toContain('data-modality="VOICE"');
    expect(markup).not.toMatch(/similarity|relevance|confidence score/i);
  });

  it("renders an unavailable notice without inventing an item", () => {
    const markup = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, {
        turn: {
          ...turn,
          state: "UNAVAILABLE",
          presentation: {
            presentation: "VOICE",
            speak: false,
            speech_text: null,
            blocks: [
              {
                kind: "NOTICE",
                state: "UNAVAILABLE",
                text: "Today is not reachable right now. Nothing was changed.",
              },
            ],
          },
        },
      }),
    );
    expect(markup).toContain("Today is not reachable right now.");
    expect(markup).not.toContain("needs attention now");
    expect(markup).toContain('data-state="UNAVAILABLE"');
  });

  it("renders clarify suggestions as suggestions, not facts", () => {
    const markup = renderToStaticMarkup(
      createElement(AssistantBlockView, {
        block: { kind: "SUGGESTIONS", queries: ["What needs my attention?"] },
      }),
    );
    expect(markup).toContain("What needs my attention?");
    expect(markup).toContain("<ul");
  });

  it("offers exact email selection only for ambiguous message candidates", () => {
    const item = {
      id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      type: "EMAIL",
      title: "Renewal notice",
      description: null,
      status: "CURRENT",
      due_at: null,
      band: "CONNECTED",
      sources: [],
    };
    const ambiguous: AssistantTurnView = {
      ...turn,
      state: "CLARIFY",
      decision: {
        kind: "CLARIFY",
        capability_id: "email.search",
        target: "knowledge.search",
        reason: "email.search.ambiguous",
        requires_approval: false,
        action_state: "NONE",
        action_id: null,
        response_state: "CLARIFY",
      },
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        blocks: [{ kind: "ITEM", item }],
      },
    };
    const selectable = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, {
        turn: ambiguous,
        onSelectEmail: vi.fn(),
      }),
    );
    expect(selectable).toContain("Select this email");
    const decision = ambiguous.decision;
    if (!decision) throw new Error("missing test decision");
    const incomplete = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, {
        turn: {
          ...ambiguous,
          decision: {
            ...decision,
            reason: "email.search.incomplete",
          },
        },
        onSelectEmail: vi.fn(),
      }),
    );
    expect(incomplete).not.toContain("Select this email");
  });

  it("renders the owning service's structured meeting preparation", () => {
    const markup = renderToStaticMarkup(
      createElement(AssistantBlockView, {
        block: {
          kind: "MEETING_BRIEFING",
          meeting: {
            commitment_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            title: "Project review",
            starts_at: "2026-10-01T14:00:00Z",
            minutes_until: 45,
            description: "Discuss the plan",
            related_commitments: [
              {
                id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                title: "Send recap",
                status: "open",
              },
            ],
            prep_points: ["Review the plan"],
          },
        },
      }),
    );
    expect(markup).toContain("Project review");
    expect(markup).toContain("45 minutes");
    expect(markup).toContain("Review the plan");
    expect(markup).toContain("Send recap");
  });

  it("shows saved subscription renewal and unverified access-end preview without an action", () => {
    const markup = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, {
        turn: {
          ...turn,
          modality: "TEXT",
          question: "When does Netflix renew?",
          presentation: {
            presentation: "TEXT",
            speak: false,
            speech_text: null,
            blocks: [
              {
                kind: "ANSWER",
                text: "Netflix: Recorded renewal date: 2026-10-15T12:00:00Z. Cancellation is not verified. The access end date is unknown.",
              },
              {
                kind: "ITEM",
                item: {
                  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                  type: "SUBSCRIPTION",
                  title: "Netflix",
                  description: "Premium",
                  status: "ACTIVE",
                  due_at: "2026-10-15T12:00:00Z",
                  band: "SUBSCRIPTION",
                  sources: [],
                },
              },
              {
                kind: "DETAILS",
                lines: [
                  "Access end date: unknown.",
                  "Provider preview estimated access end: 2026-10-16T12:00:00Z. This is not verified.",
                ],
              },
            ],
          },
        },
        onSelectEmail: vi.fn(),
      }),
    );
    expect(markup).toContain("Recorded renewal date");
    expect(markup).toContain("Access end date: unknown");
    expect(markup).toContain("Provider preview estimated access end");
    expect(markup).toContain("This is not verified");
    expect(markup).not.toContain("Select this email");
  });

  it("renders a News claim with its verification label and source selector", () => {
    const markup = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, {
        turn: {
          ...turn,
          modality: "TEXT",
          question: "Why is Jane Doe trending?",
          presentation: {
            presentation: "TEXT",
            speak: false,
            speech_text: null,
            blocks: [
              {
                kind: "ANSWER",
                text: "Jane Doe announces a project — attributed to Example Publisher. Trending activity does not verify the headline.",
              },
              {
                kind: "DETAILS",
                lines: [
                  "ATTRIBUTED: Jane Doe announced the project — Example Publisher",
                ],
              },
              {
                kind: "CITATIONS",
                citations: [
                  {
                    provider: "navox",
                    source_type: "NEWS_CLAIM",
                    external_resource_id: "https://publisher.example/jane",
                    evidence_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    connection_id: null,
                    observed_at: "2026-09-30T11:30:00Z",
                  },
                ],
              },
            ],
          },
        },
      }),
    );
    expect(markup).toContain("attributed to Example Publisher");
    expect(markup).toContain("Trending activity does not verify");
    expect(markup).toContain("ATTRIBUTED: Jane Doe announced");
    expect(markup).toContain("NEWS_CLAIM");
    expect(markup).toContain('href="https://publisher.example/jane"');
    expect(markup).toContain('rel="noopener noreferrer"');
    expect(markup).toContain('referrerPolicy="no-referrer"');
  });

  it("links a News item through the authorized story route", () => {
    const markup = renderToStaticMarkup(
      createElement(AssistantBlockView, {
        block: {
          kind: "ITEM",
          item: {
            id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            type: "NEWS_STORY",
            title: "Current story",
            description: null,
            status: "ATTRIBUTED",
            due_at: null,
            band: "TRENDING",
            sources: [],
          },
        },
      }),
    );
    expect(markup).toContain(
      'href="/news/stories/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"',
    );
  });
});

describe("click-to-speak replay control", () => {
  it("offers Speak for an eligible answer only when a handler is provided", () => {
    const onSpeak = vi.fn();
    const withHandler = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, { turn, onSpeak }),
    );
    expect(withHandler).toContain("Speak the answer");
    expect(withHandler).toContain("Speak the answer to");
    expect(withHandler).toContain('type="button"');

    const withoutHandler = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, { turn }),
    );
    expect(withoutHandler).not.toContain("Speak the answer");
  });

  it("offers Speak for a typed answer so it stays silent until clicked", () => {
    const typed: AssistantTurnView = {
      ...turn,
      modality: "TEXT",
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
      },
    };
    const markup = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, { turn: typed, onSpeak: vi.fn() }),
    );
    expect(markup).toContain("Speak the answer");
    expect(markup).toContain("1 item needs attention now.");
  });

  it("never offers Speak for a notice-only turn", () => {
    const notice: AssistantTurnView = {
      ...turn,
      state: "UNAVAILABLE",
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        blocks: [
          {
            kind: "NOTICE",
            state: "UNAVAILABLE",
            text: "Today is not reachable right now. Nothing was changed.",
          },
        ],
      },
    };
    const markup = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, { turn: notice, onSpeak: vi.fn() }),
    );
    expect(markup).not.toContain("Speak the answer");
    expect(markup).toContain("Today is not reachable right now.");
  });
});

describe("voice status copy", () => {
  it("names every visible state", () => {
    expect(assistantVoiceLabel("LISTENING")).toMatch(/recording/i);
    expect(assistantVoiceLabel("LISTENING")).toMatch(
      /press the microphone again/i,
    );
    expect(assistantVoiceLabel("TRANSCRIBING")).toMatch(/transcribing/i);
    expect(assistantVoiceLabel("THINKING")).toMatch(/navox information/i);
    expect(assistantVoiceLabel("SPEAKING")).toMatch(/interrupts it/i);
    expect(assistantVoiceLabel("MUTED")).toMatch(/muted/i);
    expect(assistantVoiceLabel("STOPPED")).toMatch(/stopped/i);
    expect(assistantVoiceLabel("UNSUPPORTED")).toMatch(
      /type your question instead/i,
    );
    expect(assistantVoiceLabel("IDLE")).toBe("");
  });
});

describe("voice controls", () => {
  it("renders the microphone, mute and stop controls before any turn", () => {
    const markup = renderToStaticMarkup(createElement(NavoXAssistant));
    expect(markup).toContain(
      'aria-pressed="false" aria-label="Start recording"',
    );
    expect(markup).toContain(
      'aria-pressed="false" aria-label="Mute spoken answers"',
    );
    expect(markup).toContain('aria-label="Stop listening and speech"');
    // Stop is a command, not a toggle.
    const labelIndex = markup.indexOf('aria-label="Stop listening and speech"');
    const stopTag = markup.slice(
      markup.lastIndexOf("<button", labelIndex),
      markup.indexOf(">", labelIndex),
    );
    expect(stopTag).not.toContain("aria-pressed");
    // The microphone is the start/finish toggle; Stop is the separate command.
    expect(markup).toContain("Recording starts only when you press");
    expect(markup).not.toContain("Finish recording and transcribe");
  });

  it("never presents an approval control for a spoken yes", () => {
    const spokenYes: AssistantTurnView = {
      ...turn,
      modality: "VOICE",
      question: "yes",
      decision: null,
      presentation: {
        presentation: "VOICE",
        speak: true,
        speech_text: "This reply cannot be sent from here.",
        blocks: [
          {
            kind: "ANSWER",
            text: "This reply cannot be sent from here.",
          },
          {
            kind: "SUGGESTIONS",
            queries: ["Which email should I reply to?"],
          },
        ],
      },
    };
    const markup = renderToStaticMarkup(
      createElement(AssistantTurnViewBlock, {
        turn: spokenYes,
        sessionId: "88888888-8888-4888-8888-888888888888",
      }),
    );
    expect(markup).toContain("yes");
    expect(markup).not.toMatch(/approve|draft reply|review exact send/i);
  });
});
