import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readStudentAssistantReply,
  type StudentAssistantApi,
  StudentAssistantController,
  type StudentAssistantEnvelope,
} from "./student-agent-client";

const user = "11111111-1111-4111-8111-111111111111",
  workspace = "22222222-2222-4222-8222-222222222222",
  session = "33333333-3333-4333-8333-333333333333",
  turn = "44444444-4444-4444-8444-444444444444",
  receipt = "55555555-5555-4555-8555-555555555555";
const account = `${workspace}:${user}`;
const envelope: StudentAssistantEnvelope = {
  turn_id: turn,
  session_id: session,
  receipt_id: receipt,
  reply: {
    status: "answer",
    source: "question_only_conversation",
    answer: "A flip-flop stores one bit.",
    links: [],
  },
};
function fake() {
  return {
    account: vi.fn(async () => account),
    open: vi.fn(async () => ({ session_id: session, account_key: account })),
    ask: vi.fn(
      async (
        _input: Parameters<StudentAssistantApi["ask"]>[0],
        _signal: AbortSignal,
      ) => structuredClone(envelope),
    ),
    forget: vi.fn(async (_id: string, _session: string) => {}),
  } satisfies StudentAssistantApi;
}
function controller(api = fake(), consent = false) {
  return {
    api,
    controller: new StudentAssistantController(
      account,
      consent,
      api,
      () => turn,
    ),
  };
}
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
describe("private text student lifecycle", () => {
  it("makes no request until an explicit question, sends no history and no default context consent", async () => {
    const { api, controller: current } = controller();
    expect(api.account).not.toHaveBeenCalled();
    current.setQuestion("Explain a flip-flop");
    await current.ask();
    expect(api.ask.mock.calls[0][0]).toEqual({
      turn_id: turn,
      session_id: session,
      question: "Explain a flip-flop",
      modality: "TEXT",
      allow_tool_context: false,
    });
    expect(current.current().question).toBe("");
    expect(current.current().reply?.answer).toContain("one bit");
    current.dispose();
  });
  it("only enables consent when an independent feature was made available", async () => {
    const disabled = controller();
    disabled.controller.setToolConsent(true);
    disabled.controller.setQuestion("Help me plan");
    await disabled.controller.ask();
    expect(disabled.api.ask.mock.calls[0][0].allow_tool_context).toBe(false);
    disabled.controller.dispose();
    const available = controller(fake(), true);
    available.controller.setToolConsent(true);
    available.controller.setQuestion("Help me plan");
    await available.controller.ask();
    expect(available.api.ask.mock.calls[0][0].allow_tool_context).toBe(true);
    expect(available.controller.current().toolConsent).toBe(false);
    available.controller.clear();
    expect(available.controller.current().toolConsent).toBe(false);
    available.controller.dispose();
  });
  it("clears visible text before a focus/account recheck resolves", async () => {
    const { api, controller: current } = controller();
    current.setQuestion("Explain");
    await current.ask();
    api.account.mockImplementation(() => new Promise(() => {}));
    const pending = current.recheck();
    expect(current.current()).toMatchObject({
      question: "",
      reply: null,
      busy: true,
      toolConsent: false,
    });
    expect(api.forget).toHaveBeenCalledWith(receipt, session);
    current.dispose();
    void pending;
  });
  it("does not expose a late response after hidden/clear, and discards its receipt", async () => {
    const { api, controller: current } = controller();
    let resolve!: (value: StudentAssistantEnvelope) => void;
    api.ask.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    current.setQuestion("Explain");
    const pending = current.ask();
    await vi.waitFor(() => expect(api.ask).toHaveBeenCalled());
    current.clear("Hidden");
    resolve(envelope);
    await pending;
    expect(current.current()).toMatchObject({
      question: "",
      reply: null,
      busy: false,
      notice: "Hidden",
    });
    expect(api.forget).toHaveBeenCalledWith(receipt, session);
    current.dispose();
  });
  it("rechecks owner after the response and suppresses data on account change", async () => {
    const { api, controller: current } = controller();
    api.account
      .mockResolvedValueOnce(account)
      .mockResolvedValueOnce("other-owner");
    current.setQuestion("Explain");
    await current.ask();
    expect(current.current()).toMatchObject({ reply: null, blocked: true });
    expect(api.forget).toHaveBeenCalledWith(receipt, session);
    current.dispose();
  });
  it("refuses another owner's newly opened session before sending any question", async () => {
    const { api, controller: current } = controller();
    api.open.mockResolvedValue({
      session_id: session,
      account_key: "other-owner",
    });
    current.setQuestion("Explain");
    await current.ask();
    expect(api.ask).not.toHaveBeenCalled();
    expect(current.current().blocked).toBe(true);
    current.dispose();
  });
  it("has one in-flight question, clears prior receipt, and never adds prior text to a follow-up", async () => {
    const { api, controller: current } = controller();
    current.setQuestion("First question");
    await current.ask();
    current.setQuestion("Second complete question");
    await current.ask();
    expect(api.open).toHaveBeenCalledTimes(1);
    expect(api.forget).toHaveBeenCalledWith(receipt, session);
    expect(api.ask.mock.calls[1][0].question).toBe("Second complete question");
    expect(Object.keys(api.ask.mock.calls[1][0])).not.toContain("history");
    current.dispose();
  });
  it("clears drafts and answers after ten minutes without browser storage", async () => {
    vi.useFakeTimers();
    const { controller: current } = controller();
    current.setQuestion("Private draft");
    await vi.advanceTimersByTimeAsync(600_000);
    expect(current.current().question).toBe("");
    current.setQuestion("Explain");
    await current.ask();
    await vi.advanceTimersByTimeAsync(600_000);
    expect(current.current().reply).toBeNull();
    current.dispose();
  });
  it("dispose aborts pending work and prevents any late reply appearing", async () => {
    const { api, controller: current } = controller();
    let resolve!: (value: StudentAssistantEnvelope) => void;
    api.ask.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    current.setQuestion("Explain");
    const pending = current.ask();
    await vi.waitFor(() => expect(api.ask).toHaveBeenCalled());
    const signal = api.ask.mock.calls[0][1];
    current.dispose();
    expect(signal.aborted).toBe(true);
    resolve(envelope);
    await pending;
    expect(current.current().reply).toBeNull();
    expect(api.forget).toHaveBeenCalledWith(receipt, session);
  });
  it("shows qualification failure as unavailable rather than fabricating an answer", async () => {
    const { api, controller: current } = controller();
    api.ask.mockResolvedValue({
      ...envelope,
      receipt_id: null,
      reply: {
        ...envelope.reply,
        status: "unavailable",
        source: "none",
        answer: "No qualified model is enabled.",
      },
    });
    current.setQuestion("Explain");
    await current.ask();
    expect(current.current().reply).toMatchObject({
      status: "unavailable",
      answer: "No qualified model is enabled.",
    });
    current.dispose();
  });
});
describe("safe browser result projection", () => {
  function raw() {
    return {
      turn_id: turn,
      session_id: session,
      receipt_id: null,
      reply: {
        status: "review",
        source: "tools",
        answer: "Review the plan before saving.",
        transcript_saved: false,
        actions_executed: false,
        observations: [
          {
            name: "plan.preview",
            effect: "review",
            data: {
              preview_hash: "secret-hash",
              approved: true,
              url: "https://evil.test",
            },
          },
        ],
      },
    };
  }
  it("retains display text and fixed review links, never raw tool JSON or approval tokens", () => {
    const result = readStudentAssistantReply(raw(), turn, session);
    expect(result.reply.links).toEqual([
      {
        href: "/planner",
        label: "Review a plan in Planner",
        note: "Preview and review every block there before saving a plan.",
      },
    ]);
    expect(JSON.stringify(result)).not.toContain("secret-hash");
    expect(JSON.stringify(result)).not.toContain("evil.test");
    expect(JSON.stringify(result)).not.toContain("approved");
  });
  it("rejects another session or turn and any claim of external execution or saved transcript", () => {
    for (const value of [
      { ...raw(), session_id: user },
      { ...raw(), turn_id: user },
      { ...raw(), reply: { ...raw().reply, actions_executed: true } },
      { ...raw(), reply: { ...raw().reply, transcript_saved: true } },
    ])
      expect(() => readStudentAssistantReply(value, turn, session)).toThrow();
  });
  function materialRaw(name = "course.materials.search") {
    return {
      ...raw(),
      reply: {
        ...raw().reply,
        status: "answer",
        answer: "Saved source matches.",
        observations: [
          {
            name,
            effect: "read",
            data: {
              course_id: user,
              query: "flip-flop",
              limit_reached: false,
              results: [
                {
                  material_id: workspace,
                  title: "Lecture 3",
                  revision: 2,
                  page: 4,
                  snippet: "Private source excerpt",
                  url: "https://evil.test/file",
                },
              ],
            },
          },
        ],
      },
    };
  }
  it("constructs source links from checked IDs and discards raw excerpts and supplied URLs", () => {
    const result = readStudentAssistantReply(materialRaw(), turn, session);
    expect(result.reply.links).toEqual([
      {
        href: `/classes/${user}?material=${workspace}&page=4&revision=2`,
        label: "Lecture 3 · page 4",
        note: "Open the saved source, version 2.",
      },
    ]);
    expect(JSON.stringify(result)).not.toContain("Private source excerpt");
    expect(JSON.stringify(result)).not.toContain("evil.test");
  });
  it("projects a bounded material page into the same exact-edition source link", () => {
    const value = materialRaw("course.materials.page");
    const row = value.reply.observations[0].data.results[0];
    const result = readStudentAssistantReply(
      {
        ...value,
        reply: {
          ...value.reply,
          observations: [
            {
              name: "course.materials.page",
              effect: "read",
              data: {
                course_id: user,
                ...row,
                text: "Saved page",
                truncated: true,
              },
            },
          ],
        },
      },
      turn,
      session,
    );
    expect(result.reply.links[0].href).toContain("page=4&revision=2");
  });
  it("rejects malformed material IDs, unsupported pages, oversized excerpts and review claims", () => {
    for (const patch of [
      { material_id: "//evil.test" },
      { page: 0 },
      { page: 101 },
      { revision: 0 },
      { title: "Lecture\n3" },
      { snippet: "x".repeat(321) },
    ]) {
      const value = materialRaw();
      Object.assign(value.reply.observations[0].data.results[0], patch);
      expect(() => readStudentAssistantReply(value, turn, session)).toThrow();
    }
    const value = materialRaw();
    value.reply.observations[0].effect = "review";
    expect(() => readStudentAssistantReply(value, turn, session)).toThrow();
    value.reply.observations[0].effect = "read";
    value.reply.status = "review";
    expect(() => readStudentAssistantReply(value, turn, session)).toThrow();
  });
});
