// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoxCallController, type VoxCallState } from "./vox-call";
import {
  readVoxTranscript,
  useVoxConversation,
  type VoxConversationModel,
} from "./vox-conversation";

let root: Root, host: HTMLDivElement, model: VoxConversationModel;
const state: VoxCallState = {
  ...new VoxCallController({
    owner: {
      userId: "10000000-0000-4000-8000-000000000001",
      workspaceId: "20000000-0000-4000-8000-000000000001",
    },
    transport: {
      available: false,
      localWakeAvailable: false,
      connect: async () => {
        throw Error();
      },
    },
    newCallId: () => "",
  }).current(),
  callId: "30000000-0000-4000-8000-000000000001",
  phase: "active",
};
function View({ value }: { value: VoxCallState }) {
  model = useVoxConversation(value);
  return (
    <div>
      {model.turns.map((t) => t.answer).join("")}
      {model.draft}
      {model.pending}
    </div>
  );
}
async function render(value = state) {
  await act(async () => root.render(<View value={value} />));
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, init) =>
      init?.method === "POST"
        ? Response.json({
            request_id: JSON.parse(init.body).request_id,
            status: "accepted",
            delivery: "queued",
          })
        : Response.json({ revision: 0, turns: [] }),
    ),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe("active-call conversation", () => {
  it("reads private server turns and preserves the draft across minimize", async () => {
    vi.mocked(fetch).mockResolvedValue(
      Response.json({
        revision: 1,
        turns: [
          {
            question: "Saved question",
            answer: "Saved answer",
            kind: "dialogue",
          },
        ],
      }),
    );
    await render();
    expect(model.turns[0].answer).toBe("Saved answer");
    act(() => model.setDraft("My next question"));
    await render({ ...state, minimized: true });
    expect(model.draft).toBe("My next question");
    expect(vi.mocked(fetch).mock.calls[0][1]?.cache).toBe("no-store");
  });
  it("drops late transcript responses after hangup", async () => {
    let resolve!: (r: Response) => void;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    await render();
    act(() => model.setDraft("Private draft"));
    await render({ ...state, phase: "ended" });
    await act(async () =>
      resolve(
        Response.json({
          revision: 1,
          turns: [
            {
              question: "Private",
              answer: "Old account answer",
              kind: "dialogue",
            },
          ],
        }),
      ),
    );
    expect(host.textContent).toBe("");
    expect(model.available).toBe(false);
  });
  it("does not carry conversation or drafts to another account", async () => {
    await render();
    act(() => model.setDraft("Account one"));
    await render({
      ...state,
      owner: {
        workspaceId: "20000000-0000-4000-8000-000000000001",
        userId: "10000000-0000-4000-8000-000000000002",
      },
    });
    expect(model.draft).toBe("");
    expect(model.turns).toEqual([]);
  });
  it("sends exactly 8000 code points while muted and blocks 8001 without truncation", async () => {
    await render({ ...state, microphoneMuted: true });
    const question = "a".repeat(8000);
    act(() => model.setDraft(question));
    await act(async () => model.send());
    const posts = vi
      .mocked(fetch)
      .mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0][1]?.body)).text).toBe(question);
    expect(model.pending).toBe(question);
    await render({ ...state, phase: "ended" });
    await render(state);
    act(() => model.setDraft("b".repeat(8001)));
    await act(async () => model.send());
    expect(
      vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
    expect(model.draft).toHaveLength(8001);
  });
  it("reuses the request id when a failed submission is explicitly retried", async () => {
    let first = true;
    vi.mocked(fetch).mockImplementation(async (_url, init) => {
      if (init?.method !== "POST")
        return Response.json({ revision: 0, turns: [] });
      if (first) {
        first = false;
        throw Error("network");
      }
      return Response.json({
        request_id: JSON.parse(String(init.body)).request_id,
        status: "accepted",
        delivery: "queued",
      });
    });
    await render();
    act(() => model.setDraft("Retry this"));
    await act(async () => model.send());
    await act(async () => model.send());
    const posts = vi
      .mocked(fetch)
      .mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2);
    expect(JSON.parse(String(posts[0][1]?.body)).request_id).toBe(
      JSON.parse(String(posts[1][1]?.body)).request_id,
    );
  });
  it("rejects malformed transcript data", () => {
    expect(() =>
      readVoxTranscript({
        revision: 1,
        turns: [{ question: null, answer: "invented", kind: "dialogue" }],
      }),
    ).toThrow();
  });
});
