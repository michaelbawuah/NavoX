// @vitest-environment jsdom

import { initialVoiceState } from "@navox/assistant-runtime/voice";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StudentQuickAskAnswer } from "../lib/student-voice";
import { studentClassDraftKey } from "../lib/student-voice";
import { StudentQuickAsk } from "./student-voice-dialog";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  request: vi.fn(),
  removeSession: vi.fn(async () => {}),
  voice: {
    stop: vi.fn(),
    dispose: vi.fn(),
    beginTurn: vi.fn(),
    markSubmitted: vi.fn(),
    answerReady: vi.fn(),
    turnFailed: vi.fn(),
    listen: vi.fn(),
    toggleListening: vi.fn(),
    speakSaved: vi.fn(),
    current: vi.fn(),
  },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("../lib/assistant-client", () => ({
  MAX_SPEECH_AUDIO_BYTES: 1024,
  deleteAssistantSession: mocks.removeSession,
  newAssistantRequestId: () => "30000000-0000-4000-8000-000000000001",
  transcribeAssistantSpeech: vi.fn(),
}));
vi.mock("../lib/assistant-controller", () => ({
  createVoiceSession: () => mocks.voice,
}));
vi.mock("../lib/assistant-speech", () => ({ createSpeechAdapter: () => ({}) }));
vi.mock("../lib/student-voice", async (original) => ({
  ...(await original<typeof import("../lib/student-voice")>()),
  studentVoiceRequest: mocks.request,
}));

const accountKey = "workspace:student";
const sessionId = "10000000-0000-4000-8000-000000000001";
const dialogDescriptors = {
  showModal: Object.getOwnPropertyDescriptor(
    HTMLDialogElement.prototype,
    "showModal",
  ),
  close: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "close"),
};
let root: Root;
let host: HTMLDivElement;
let answer: StudentQuickAskAnswer;
let onClose: ReturnType<typeof vi.fn<() => void>>;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  for (const name of ["showModal", "close"] as const)
    Object.defineProperty(HTMLDialogElement.prototype, name, {
      configurable: true,
      value: vi.fn(),
    });
  for (const fn of Object.values(mocks.voice)) fn.mockReset();
  mocks.voice.current.mockReturnValue(initialVoiceState);
  mocks.push.mockReset();
  mocks.request.mockReset();
  mocks.removeSession.mockClear();
  sessionStorage.clear();
  answer = {
    kind: "class_draft",
    answer: "Review the class details before saving.",
    class_draft: "Practice lab meets Friday at noon.",
    saved: false,
    receipt_id: null,
  };
  mocks.request.mockImplementation(async (path: string) => {
    if (path === "/api/v1/student-voice/session")
      return { session_id: sessionId, account_key: accountKey };
    if (path === "/api/v1/student-voice/ask") return answer;
    throw new Error(`Unexpected quick-ask request: ${path}`);
  });
  onClose = vi.fn();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  sessionStorage.clear();
  vi.restoreAllMocks();
  for (const name of ["showModal", "close"] as const) {
    const original = dialogDescriptors[name];
    if (original)
      Object.defineProperty(HTMLDialogElement.prototype, name, original);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
  }
  vi.unstubAllGlobals();
});

function button(text: string) {
  const result = Array.from(host.querySelectorAll("button")).find(
    (element) => element.textContent?.trim() === text,
  );
  if (!result) throw new Error(`Missing quick-ask control: ${text}`);
  return result;
}

async function ask(save = false) {
  await act(async () => {
    root.render(createElement(StudentQuickAsk, { accountKey, onClose }));
  });
  if (save) {
    const checkbox = host.querySelector<HTMLInputElement>(
      'input[type="checkbox"]',
    );
    if (!checkbox) throw new Error("Missing explicit save control");
    act(() => checkbox.click());
  }
  await act(async () => button("What class do I have now?").click());
  expect(mocks.request).toHaveBeenCalledWith(
    "/api/v1/student-voice/ask",
    "POST",
    expect.objectContaining({ save }),
    expect.any(AbortSignal),
  );
}

describe("Quick Ask client navigation handoffs", () => {
  it("stops and closes Quick Ask when the Timetable footer link is selected", async () => {
    await ask();
    const link = host.querySelector<HTMLAnchorElement>(
      'footer a[href="/classes"]',
    );
    if (!link) throw new Error("Missing Timetable link");
    // Next's router context is outside this component test. Prevent jsdom's
    // document navigation while exercising the real link's cleanup handler.
    link.addEventListener("click", (event) => event.preventDefault(), {
      once: true,
    });
    act(() => link.click());
    expect(mocks.voice.stop).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mocks.voice.stop.mock.invocationCallOrder[0]).toBeLessThan(
      onClose.mock.invocationCallOrder[0],
    );
  });

  it("carries the owner-bound class draft and closes capture before client navigation", async () => {
    await ask();
    act(() => button("Review class details").click());
    expect(
      JSON.parse(
        sessionStorage.getItem(studentClassDraftKey(accountKey)) ?? "null",
      ),
    ).toMatchObject({
      accountKey,
      transcript: answer.class_draft,
      createdAt: expect.any(String),
    });
    expect(mocks.push).toHaveBeenCalledExactlyOnceWith("/classes?add=1");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mocks.voice.stop.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.push.mock.invocationCallOrder[0],
    );
    expect(onClose.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.push.mock.invocationCallOrder[0],
    );
  });

  it("opens a saved session with client navigation after an explicitly saved quick ask", async () => {
    answer = { ...answer, saved: true };
    await ask(true);
    act(() => button("Open saved conversation").click());
    expect(sessionStorage.getItem("navox.assistant.session.v1")).toBe(
      sessionId,
    );
    expect(mocks.push).toHaveBeenCalledExactlyOnceWith("/navox");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mocks.voice.stop.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.push.mock.invocationCallOrder[0],
    );
    expect(mocks.removeSession).not.toHaveBeenCalled();
  });

  it("keeps the question visible instead of navigating when the draft cannot be carried over", async () => {
    await ask();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Storage unavailable");
    });
    act(() => button("Review class details").click());
    expect(host.textContent).toContain(
      "This browser could not carry the note over.",
    );
    expect(mocks.push).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(host.textContent).toContain(answer.answer);
  });
});
