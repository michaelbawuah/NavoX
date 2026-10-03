import {
  initialVoiceState,
  type VoiceSessionState,
} from "@navox/assistant-runtime/voice";
import type {
  AssistantMessageResponse,
  AssistantModality,
  AssistantTurnView,
} from "@navox/contracts";
import { describe, expect, it, vi } from "vitest";
import {
  AssistantRequestIdError,
  AssistantTurnLedger,
} from "./assistant-client";
import {
  canSpeakTurn,
  createAssistantTurnRunner,
  createVoiceSession,
  speechStartForTurn,
  speechTextForTurn,
  voiceControls,
  voiceTurnRequest,
} from "./assistant-controller";
import type {
  SpeechAdapter,
  SpeechHandlers,
  SynthesizeSpeech,
  TranscribeSpeech,
} from "./assistant-speech";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

/** The saved turn selector every default fixture uses. */
const TURN_ID = "88888888-8888-4888-8888-888888888888";
/** One bounded MP3 stand-in for the injected saved-turn speech fetch. */
const MP3 = Uint8Array.from([0x49, 0x44, 0x33, 0x04]);

function turn(overrides: Partial<AssistantTurnView> = {}): AssistantTurnView {
  return {
    id: TURN_ID,
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
      delivery: "AUTOMATIC",
      blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
    },
    action_refs: [],
    created_at: "2026-09-30T12:00:00.000Z",
    ...overrides,
  };
}

function harness(
  submit: (
    sessionId: string,
    input: { requestId: string },
  ) => Promise<AssistantMessageResponse>,
) {
  const ledger = new AssistantTurnLedger();
  const appended: AssistantTurnView[] = [];
  const notices: string[] = [];
  const spoken: string[] = [];
  const explicit: boolean[] = [];
  const busy: boolean[] = [];
  const runner = createAssistantTurnRunner({
    submit: submit as never,
    ledger,
    timezone: () => "America/New_York",
    onTurn: (value) => appended.push(value),
    onNotice: (message) => notices.push(message),
    onBusy: (value) => busy.push(value),
    onSpeak: (value, requested) => {
      spoken.push(value.id);
      explicit.push(requested);
    },
  });
  return { runner, ledger, appended, notices, spoken, explicit, busy };
}

describe("assistant turn runner", () => {
  it.each(["TEXT", "VOICE"] as const)(
    "refuses an oversized %s question without submitting a prefix",
    async (modality) => {
      const submit = vi.fn();
      const context = harness(submit);
      const question = "x".repeat(501);
      await context.runner.run({
        sessionId: "session-a",
        modality,
        text: question,
      });
      expect(submit).not.toHaveBeenCalled();
      expect(context.appended).toEqual([]);
      expect(context.notices).toEqual([
        "That question is longer than 500 characters. Shorten it before sending.",
      ]);
      expect(question).toHaveLength(501);
    },
  );

  it("submits every character of a question at the input limit", async () => {
    const submit = vi.fn(async () => ({
      session_id: "session-a",
      turn: turn(),
      replay: false,
    }));
    const context = harness(submit);
    const question = "x".repeat(500);
    await context.runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: question,
    });
    expect(submit).toHaveBeenCalledWith(
      "session-a",
      expect.objectContaining({ text: question }),
    );
    expect(context.notices).toEqual([]);
  });
  it("drops a stale completion after clear and never speaks it", async () => {
    const gate = deferred<AssistantMessageResponse>();
    const context = harness(() => gate.promise);
    const running = context.runner.run({
      sessionId: "session-a",
      modality: "VOICE",
      text: "What am I missing today?",
    });
    expect(context.runner.generation()).toBe(0);
    context.runner.invalidate();
    expect(context.runner.generation()).toBe(1);
    gate.resolve({ session_id: "session-a", turn: turn(), replay: false });
    await running;

    expect(context.appended).toEqual([]);
    expect(context.spoken).toEqual([]);
    expect(context.notices).toEqual([]);
    expect(context.busy.at(-1)).toBe(false);
  });

  it("drops a stale failure without notifying the operator", async () => {
    let reject!: (reason: unknown) => void;
    const failing = new Promise<AssistantMessageResponse>(
      (_resolve, refuse) => {
        reject = refuse;
      },
    );
    const context = harness(() => failing);
    const running = context.runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: "What am I missing today?",
    });
    context.runner.invalidate();
    reject(new Error("late failure"));
    await running;

    expect(context.notices).toEqual([]);
    expect(context.appended).toEqual([]);
    expect(context.busy.at(-1)).toBe(false);
  });

  it("appends and speaks a current voice answer exactly once", async () => {
    const context = harness(async () => ({
      session_id: "session-a",
      turn: turn(),
      replay: false,
    }));
    await context.runner.run({
      sessionId: "session-a",
      modality: "VOICE",
      text: "What am I missing today?",
    });
    expect(context.appended).toHaveLength(1);
    expect(context.spoken).toEqual([TURN_ID]);
    // A spoken question's answer follows Voice Mode rather than an explicit ask.
    expect(context.explicit).toEqual([false]);
    expect(context.notices).toEqual([]);
    expect(context.busy).toEqual([true, false]);
  });

  it("flags a typed read-aloud request as explicit operator speech", async () => {
    const context = harness(async () => ({
      session_id: "session-a",
      turn: turn({
        modality: "TEXT",
        presentation: {
          presentation: "BOTH",
          speak: true,
          speech_text: "1 item needs attention now.",
          delivery: "SPEAK",
          blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
        },
      }),
      replay: false,
    }));
    await context.runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: "read it to me",
    });
    expect(context.spoken).toEqual([TURN_ID]);
    expect(context.explicit).toEqual([true]);
  });

  it("treats an explicit cue in a voice turn as request, not Voice Mode", async () => {
    // The server records SPEAK for a spoken "read it to me", so the page must
    // not lose that intent just because the turn arrived by voice.
    const context = harness(async () => ({
      session_id: "session-a",
      turn: turn({
        modality: "VOICE",
        presentation: {
          presentation: "BOTH",
          speak: true,
          speech_text: "1 item needs attention now.",
          delivery: "SPEAK",
          blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
        },
      }),
      replay: false,
    }));
    await context.runner.run({
      sessionId: "session-a",
      modality: "VOICE",
      text: "read it to me",
    });
    expect(context.spoken).toEqual([TURN_ID]);
    expect(context.explicit).toEqual([true]);
  });

  it("keeps a spoken question automatic rather than explicit", async () => {
    const context = harness(async () => ({
      session_id: "session-a",
      turn: turn({
        modality: "VOICE",
        presentation: {
          presentation: "BOTH",
          speak: true,
          speech_text: "1 item needs attention now.",
          delivery: "AUTOMATIC",
          blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
        },
      }),
      replay: false,
    }));
    await context.runner.run({
      sessionId: "session-a",
      modality: "VOICE",
      text: "what am I missing today",
    });
    expect(context.explicit).toEqual([false]);
  });

  it("never asks to speak a suppressed visual answer", async () => {
    const context = harness(async () => ({
      session_id: "session-a",
      turn: turn({
        presentation: {
          presentation: "TEXT",
          speak: false,
          speech_text: null,
          delivery: "SUPPRESS",
          blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
        },
      }),
      replay: false,
    }));
    await context.runner.run({
      sessionId: "session-a",
      modality: "VOICE",
      text: "what am I missing today - don't read it aloud",
    });
    expect(context.spoken).toEqual([]);
    expect(context.appended).toHaveLength(1);
  });

  it("keeps a typed answer silent until the operator asks for it", async () => {
    const typed = turn({
      modality: "TEXT",
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        delivery: "AUTOMATIC",
        blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
      },
    });
    const context = harness(async () => ({
      session_id: "session-a",
      turn: typed,
      replay: false,
    }));
    await context.runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: "What am I missing today?",
    });
    expect(context.appended).toHaveLength(1);
    expect(context.spoken).toEqual([]);

    // The explicit Speak control offers the same text on demand.
    expect(canSpeakTurn(typed)).toBe(true);
    expect(speechTextForTurn(typed)).toBe("1 item needs attention now.");
  });

  it("never offers a Speak control for a notice-only turn", () => {
    const unavailable = turn({
      state: "UNAVAILABLE",
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        delivery: "AUTOMATIC",
        blocks: [
          {
            kind: "NOTICE",
            state: "UNAVAILABLE",
            text: "Today is not reachable right now. Nothing was changed.",
          },
        ],
      },
    });
    expect(canSpeakTurn(unavailable)).toBe(false);
    expect(speechTextForTurn(unavailable)).toBeNull();
  });

  it("clamps spoken replay text to the runtime bound", () => {
    const long = turn({
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        delivery: "AUTOMATIC",
        blocks: [{ kind: "ANSWER", text: "x".repeat(2000) }],
      },
    });
    expect(speechTextForTurn(long)).toHaveLength(600);
  });

  it("never offers a spoken answer that would need approval", () => {
    const withheld = turn({
      state: "WITHHELD",
      decision: {
        kind: "WITHHELD",
        capability_id: null,
        target: null,
        reason: "approval.required",
        requires_approval: true,
        action_state: "PENDING_APPROVAL",
        action_id: "action-1",
        response_state: "WITHHELD",
      },
      presentation: {
        presentation: "VOICE",
        speak: false,
        speech_text: null,
        delivery: "AUTOMATIC",
        blocks: [{ kind: "ANSWER", text: "Send the email now." }],
      },
    });
    expect(canSpeakTurn(withheld)).toBe(false);
    expect(speechTextForTurn(withheld)).toBeNull();
  });

  it("ignores an empty question and an empty session", async () => {
    const submit = vi.fn();
    const context = harness(submit as never);
    await context.runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: "   ",
    });
    await context.runner.run({
      sessionId: null,
      modality: "TEXT",
      text: "hello",
    });
    expect(submit).not.toHaveBeenCalled();
    expect(context.busy).toEqual([]);
  });

  it("surfaces an unusable request id as a notice instead of a silent failure", async () => {
    const notices: { message: string; modality: string }[] = [];
    const ledger = {
      requestIdFor: () => {
        throw new AssistantRequestIdError(
          "This browser cannot create a secure request identifier.",
        );
      },
      resolve: () => {},
      forget: () => {},
    } as unknown as AssistantTurnLedger;
    const submit = vi.fn();
    const runner = createAssistantTurnRunner({
      submit: submit as never,
      ledger,
      onTurn: () => {},
      onNotice: (message, modality) => notices.push({ message, modality }),
      onBusy: () => {},
      onSpeak: () => {},
      messageForError: (error) =>
        error instanceof AssistantRequestIdError
          ? error.message
          : "generic failure",
    });

    await expect(
      runner.run({ sessionId: "session-a", modality: "TEXT", text: "hello" }),
    ).resolves.toBeUndefined();
    expect(submit).not.toHaveBeenCalled();
    expect(notices).toEqual([
      {
        message: "This browser cannot create a secure request identifier.",
        modality: "TEXT",
      },
    ]);
  });

  it("reuses one request id for a retry after a failure", async () => {
    const ids: string[] = [];
    const ledger = new AssistantTurnLedger();
    const runner = createAssistantTurnRunner({
      submit: async (_sessionId, input) => {
        ids.push(input.requestId);
        if (ids.length === 1) throw new Error("temporary failure");
        return { session_id: "session-a", turn: turn(), replay: false };
      },
      ledger,
      onTurn: () => {},
      onNotice: () => {},
      onBusy: () => {},
      onSpeak: () => {},
    });
    await runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: "hello",
    });
    await runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: "hello",
    });
    expect(ids).toHaveLength(2);
    expect(ids[0]).toBe(ids[1]);
  });

  it("keeps an email selection's two pointers on the submitted turn", async () => {
    const submit = vi.fn(async () => ({
      session_id: "session-a",
      turn: turn(),
      replay: false,
    }));
    const context = harness(submit);
    const referents = [
      "88888888-8888-4888-8888-888888888888",
      "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    ];
    await context.runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: "Select an email",
      referents,
    });
    expect(submit).toHaveBeenCalledWith(
      "session-a",
      expect.objectContaining({ referents }),
    );
  });

  it("starts a new request id after invalidation", async () => {
    const ids: string[] = [];
    const ledger = new AssistantTurnLedger();
    const runner = createAssistantTurnRunner({
      submit: async (_sessionId, input) => {
        ids.push(input.requestId);
        return { session_id: "session-a", turn: turn(), replay: false };
      },
      ledger,
      onTurn: () => {},
      onNotice: () => {},
      onBusy: () => {},
      onSpeak: () => {},
    });
    await runner.run({
      sessionId: "session-a",
      modality: "TEXT",
      text: "hello",
    });
    runner.invalidate();
    await runner.run({
      sessionId: "session-b",
      modality: "TEXT",
      text: "hello",
    });
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
  });
});

interface FakeAdapter extends SpeechAdapter {
  log: string[];
  bargeSpeech: (() => void) | null;
  spoken: string[];
  /** The saved-turn fetcher the session handed to the last playback. */
  synthesize: SynthesizeSpeech | null;
  /** What the next finish click reports: true only when an upload started. */
  finishResult: { value: boolean };
  /** Every microphone attempt, oldest first: its handlers and its uploader. */
  capture: { handlers: SpeechHandlers; transcribe: TranscribeSpeech }[];
  /** Handler sets of every utterance, oldest first. */
  speech: Omit<SpeechHandlers, "onTranscript">[];
  /** Simulates the Next route returning a bounded transcript. */
  deliverTranscript(text: string): void;
  /** Simulates a capture ending without a usable transcript. */
  failCapture(reason: string): void;
  endCapture(): void;
  /** Simulates the browser finishing or failing the current utterance. */
  finishSpeech(): void;
  failSpeech(): void;
}

function fakeAdapter(overrides: Partial<SpeechAdapter> = {}): FakeAdapter {
  const log: string[] = [];
  const spoken: string[] = [];
  const capture: { handlers: SpeechHandlers; transcribe: TranscribeSpeech }[] =
    [];
  const speech: Omit<SpeechHandlers, "onTranscript">[] = [];
  const finishResult = { value: true };
  return {
    captureSupported: true,
    synthesisSupported: true,
    captureReason: null,
    synthesisReason: null,
    log,
    bargeSpeech: null,
    spoken,
    synthesize: null,
    finishResult,
    capture,
    speech,
    startListening(handlers, transcribe) {
      log.push("startListening");
      capture.push({ handlers, transcribe });
    },
    startAutomaticListening(handlers, transcribe) {
      log.push("startAutomaticListening");
      capture.push({ handlers, transcribe });
    },
    startBargeIn(handlers, transcribe, onSpeech) {
      log.push("startBargeIn");
      capture.push({ handlers, transcribe });
      this.bargeSpeech = onSpeech;
    },
    finishListening() {
      log.push("finishListening");
      return finishResult.value;
    },
    stopListening() {
      log.push("stopListening");
    },
    speak(turnId, handlers, synthesize) {
      log.push(`speak:${turnId}`);
      spoken.push(turnId);
      speech.push(handlers);
      this.synthesize = synthesize;
    },
    stopSpeaking() {
      log.push("stopSpeaking");
    },
    deliverTranscript(text) {
      capture.at(-1)?.handlers.onTranscript(text);
    },
    failCapture(reason) {
      capture.at(-1)?.handlers.onError(reason);
    },
    endCapture() {
      capture.at(-1)?.handlers.onEnd();
    },
    finishSpeech() {
      speech.at(-1)?.onEnd();
    },
    failSpeech() {
      speech.at(-1)?.onError("The spoken answer could not be played.");
    },
    ...overrides,
  };
}

function voiceTurn(
  modality: AssistantModality,
  text: string,
): AssistantTurnView {
  return {
    id: TURN_ID,
    sequence: 1,
    modality,
    state: "READY",
    question: text,
    plan: null,
    decision: null,
    presentation: {
      presentation: modality === "VOICE" ? "VOICE" : "TEXT",
      speak: modality === "VOICE",
      speech_text: modality === "VOICE" ? "1 item needs attention now." : null,
      delivery: "AUTOMATIC",
      blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
    },
    action_refs: [],
    created_at: "2026-09-30T12:00:00.000Z",
  };
}

/**
 * Mirrors the page wiring: a microphone click opens the session, the browser
 * delivers one transcript, and only a live turn may speak its answer.
 */
function voiceRig(
  submit: (
    sessionId: string,
    input: {
      requestId: string;
      text: string;
      modality: AssistantModality;
      timezone: string | null;
      referents?: string[];
    },
  ) => Promise<AssistantMessageResponse> = async (_sessionId, input) => ({
    session_id: "session-a",
    turn: voiceTurn(input.modality, input.text),
    replay: false,
  }),
) {
  const adapter = fakeAdapter();
  const states: VoiceSessionState[] = [];
  const notices: string[] = [];
  const transcripts: string[] = [];
  const appended: AssistantTurnView[] = [];
  const pendingToken = { current: 0 };
  let pending: Promise<void> | null = null;
  const transcribe = vi.fn(async () => "What am I missing today?");
  const synthesize = vi.fn(async () => MP3);

  const session = createVoiceSession({
    adapter,
    transcribe,
    synthesize,
    onState: (state) => states.push(state),
    onNotice: (message) => notices.push(message),
    onTranscript: (request) => {
      transcripts.push(request.text);
      pendingToken.current = session.beginTurn();
      session.markSubmitted();
      pending = runner.run({
        sessionId: "session-a",
        modality: request.modality,
        text: request.text,
        referents: request.referents,
      });
    },
  });

  const runner = createAssistantTurnRunner({
    submit,
    ledger: new AssistantTurnLedger(),
    timezone: () => "America/New_York",
    onTurn: (turn) => {
      appended.push(turn);
      if (turn.modality === "VOICE") session.answerReady();
    },
    onNotice: (message, modality) => {
      notices.push(message);
      if (modality === "VOICE") session.turnFailed(message);
    },
    onBusy: () => {},
    onSpeak: (value) => session.speakAutomatic(value, pendingToken.current),
  });

  const settle = async () => {
    const running = pending;
    pending = null;
    if (running) await running;
  };

  return {
    adapter,
    session,
    transcribe,
    synthesize,
    states,
    notices,
    transcripts,
    appended,
    settle,
    /** A microphone click followed by one browser transcript. */
    async voiceTurn(text: string) {
      session.toggleListening();
      adapter.deliverTranscript(text);
      await settle();
    },
    /** A typed turn never touches the voice session. */
    async typedTurn(text: string) {
      await runner.run({
        sessionId: "session-a",
        modality: "TEXT",
        text,
      });
    },
    startListening() {
      session.toggleListening();
    },
    /** The operator's second click: finish the clip and transcribe it. */
    finish() {
      session.toggleListening();
    },
  };
}

describe("voice session lifecycle", () => {
  it("reports an oversized transcript and never submits a shortened question", async () => {
    const submit = vi.fn();
    const rig = voiceRig(submit);
    await rig.voiceTurn("x".repeat(501));
    expect(submit).not.toHaveBeenCalled();
    expect(rig.transcripts).toEqual([]);
    expect(rig.appended).toEqual([]);
    expect(rig.notices).toEqual([
      "That question is longer than 500 characters. Shorten it before sending.",
    ]);
    expect(rig.session.current().transcript).toBeNull();
  });
  it("keeps a typed turn silent and speaks a live voice turn", async () => {
    const rig = voiceRig();
    await rig.typedTurn("What am I missing today?");
    expect(rig.adapter.spoken).toEqual([]);
    expect(rig.session.current().state).toBe("IDLE");

    await rig.voiceTurn("What am I missing today?");
    expect(rig.adapter.spoken).toEqual([TURN_ID]);
    expect(rig.session.current().state).toBe("SPEAKING");
  });

  it("gates automatic speech on Voice Mode, mute and an explicit request", () => {
    const state = initialVoiceState();
    expect(speechStartForTurn(state, false)).toBe("AUTOMATIC");
    // Voice Mode off: a spoken answer waits for the Read aloud control.
    expect(speechStartForTurn({ ...state, voiceMode: false }, false)).toBe(
      "NONE",
    );
    // An explicit request still speaks: it is the operator's own instruction.
    expect(speechStartForTurn({ ...state, voiceMode: false }, true)).toBe(
      "MANUAL",
    );
    // Mute always wins, even for an explicit request.
    expect(speechStartForTurn({ ...state, muted: true }, true)).toBe("NONE");
    expect(speechStartForTurn({ ...state, state: "STOPPED" }, false)).toBe(
      "NONE",
    );
    // Stop does not silence an explicit click, and neither does a dead mic.
    expect(speechStartForTurn({ ...state, state: "STOPPED" }, true)).toBe(
      "MANUAL",
    );
  });

  it("turns acoustic interruption into a new turn in the same session", async () => {
    const rig = voiceRig();
    rig.session.speakManually(voiceTurn("VOICE", "first answer"));
    rig.session.armBargeIn();
    expect(rig.adapter.log).toContain("startBargeIn");
    expect(rig.session.current().state).toBe("SPEAKING");
    rig.adapter.bargeSpeech?.();
    expect(rig.session.current().state).toBe("LISTENING");
    rig.adapter.deliverTranscript("Actually, show the next one.");
    await rig.settle();
    expect(rig.transcripts).toEqual(["Actually, show the next one."]);
    expect(rig.appended.at(-1)?.question).toBe("Actually, show the next one.");
  });

  it("automatically captures a Hands-Free question after wake", async () => {
    const rig = voiceRig();
    rig.session.stop();
    rig.session.resumeForWake();
    rig.session.listenAutomatically();
    expect(rig.adapter.log).toContain("startAutomaticListening");
    rig.adapter.deliverTranscript("What class do I have next?");
    await rig.settle();
    expect(rig.transcripts).toEqual(["What class do I have next?"]);
  });

  it("cancels playback and listens when the microphone is pressed during speech", async () => {
    const rig = voiceRig();
    await rig.voiceTurn("What am I missing today?");
    expect(rig.session.current().state).toBe("SPEAKING");

    rig.startListening();
    expect(rig.session.current().state).toBe("LISTENING");
    expect(rig.adapter.log).toContain("stopSpeaking");
    expect(rig.adapter.log).toContain("startListening");
    expect(rig.adapter.log.indexOf("stopSpeaking")).toBeLessThan(
      rig.adapter.log.indexOf("startListening"),
    );
  });

  it("cancels an active answer on mute and keeps later answers silent", async () => {
    const rig = voiceRig();
    await rig.voiceTurn("What am I missing today?");
    expect(rig.session.current().state).toBe("SPEAKING");

    rig.session.setMuted(true);
    expect(rig.adapter.log).toContain("stopSpeaking");
    expect(rig.session.current().state).toBe("MUTED");
    expect(voiceControls(rig.session.current()).readAloudAvailable).toBe(false);

    await rig.voiceTurn("What am I missing today?");
    expect(rig.adapter.spoken).toEqual([TURN_ID]);
    expect(rig.session.current().state).toBe("MUTED");

    rig.session.setMuted(false);
    expect(rig.session.current().state).toBe("IDLE");
    expect(voiceControls(rig.session.current()).readAloudAvailable).toBe(true);
    await rig.voiceTurn("What am I missing today?");
    expect(rig.adapter.spoken).toEqual([TURN_ID, TURN_ID]);
  });

  it("stays silent when mute arrives before the answer", async () => {
    const gate = deferred<AssistantMessageResponse>();
    const rig = voiceRig(() => gate.promise);
    rig.startListening();
    rig.adapter.deliverTranscript("What am I missing today?");
    expect(rig.session.current().state).toBe("THINKING");

    rig.session.setMuted(true);
    expect(rig.session.current().state).toBe("THINKING");
    expect(rig.session.current().muted).toBe(true);

    gate.resolve({
      session_id: "session-a",
      turn: voiceTurn("VOICE", "What am I missing today?"),
      replay: false,
    });
    await rig.settle();
    expect(rig.adapter.spoken).toEqual([]);
    expect(rig.session.current().state).toBe("MUTED");
  });

  it("releases the microphone on Stop during listening", () => {
    const rig = voiceRig();
    rig.startListening();
    expect(rig.session.current().state).toBe("LISTENING");

    rig.session.stop();
    expect(rig.adapter.log).toContain("stopListening");
    expect(rig.session.current().state).toBe("STOPPED");

    // A transcript that arrives after Stop never opens a turn.
    rig.adapter.deliverTranscript("late words");
    rig.adapter.endCapture();
    expect(rig.transcripts).toEqual([]);
    expect(rig.appended).toEqual([]);
    expect(rig.session.current().state).toBe("STOPPED");
  });

  it("releases speech on Stop during playback and ignores a late end", async () => {
    const rig = voiceRig();
    await rig.voiceTurn("What am I missing today?");
    expect(rig.session.current().state).toBe("SPEAKING");

    rig.session.stop();
    expect(rig.adapter.log).toContain("stopSpeaking");
    expect(rig.session.current().state).toBe("STOPPED");

    rig.adapter.finishSpeech();
    expect(rig.session.current().state).toBe("STOPPED");
  });

  it("refuses to speak an answer that arrives after Stop", async () => {
    const gate = deferred<AssistantMessageResponse>();
    const rig = voiceRig(() => gate.promise);
    rig.startListening();
    rig.adapter.deliverTranscript("What am I missing today?");
    expect(rig.session.current().state).toBe("THINKING");

    rig.session.stop();
    gate.resolve({
      session_id: "session-a",
      turn: voiceTurn("VOICE", "What am I missing today?"),
      replay: false,
    });
    await rig.settle();
    expect(rig.adapter.spoken).toEqual([]);
    expect(rig.appended).toHaveLength(1);
    expect(rig.session.current().state).toBe("STOPPED");

    await rig.voiceTurn("What am I missing today?");
    expect(rig.adapter.spoken).toEqual([TURN_ID]);
    expect(rig.session.current().state).toBe("SPEAKING");
  });

  it("drops late capture and synthesis callbacks after clear or unmount", async () => {
    const rig = voiceRig();
    await rig.voiceTurn("What am I missing today?");
    const published = rig.states.length;

    rig.session.dispose();
    expect(rig.adapter.log).toContain("stopSpeaking");
    expect(rig.adapter.log).toContain("stopListening");

    rig.adapter.finishSpeech();
    rig.adapter.deliverTranscript("late words");
    rig.adapter.endCapture();
    expect(rig.states).toHaveLength(published);
    expect(rig.transcripts).toEqual(["What am I missing today?"]);

    // A cleared page starts from a fresh, silent session.
    const next = createVoiceSession({
      adapter: rig.adapter,
      transcribe: async () => "unused",
      synthesize: async () => MP3,
      onState: (state) => rig.states.push(state),
      onNotice: (message) => rig.notices.push(message),
      onTranscript: (request) => rig.transcripts.push(request.text),
    });
    expect(next.current()).toEqual({
      state: "IDLE",
      microphoneSupported: true,
      muted: false,
      voiceMode: true,
      transcript: null,
      error: null,
      turn: 0,
    });
  });

  it("falls back to typed input when the browser cannot capture audio", () => {
    const adapter = fakeAdapter({
      captureSupported: false,
      captureReason: "This browser cannot record from the microphone.",
    });
    const session = createVoiceSession({
      adapter,
      transcribe: async () => "unused",
      synthesize: async () => MP3,
      onState: () => {},
      onNotice: () => {},
      onTranscript: () => {},
    });
    expect(session.current().state).toBe("UNSUPPORTED");
    expect(session.current().error).toMatch(
      /cannot record from the microphone/i,
    );
    expect(voiceControls(session.current()).microphoneDisabled).toBe(true);

    session.toggleListening();
    expect(adapter.log).not.toContain("startListening");
    expect(session.current().state).toBe("UNSUPPORTED");
  });

  it("reads one saved draft selector through the same playback lifecycle", async () => {
    const rig = voiceRig();
    const starter = {
      synthesize: vi.fn(async () => MP3),
      onEnd: vi.fn(),
      onError: vi.fn(),
    };
    rig.session.speakSaved(starter);
    expect(rig.session.current().state).toBe("SPEAKING");
    expect(rig.adapter.spoken).toEqual(["saved-selector"]);
    // The adapter calls the caller's closure with only the abort signal.
    const signal = new AbortController().signal;
    await rig.adapter.synthesize?.("saved-selector", signal);
    expect(starter.synthesize).toHaveBeenCalledWith(signal);
    rig.adapter.finishSpeech();
    expect(starter.onEnd).toHaveBeenCalledOnce();
    expect(rig.session.current().state).toBe("IDLE");
  });

  it("cancels playback on a draft edit without stopping the session", () => {
    const rig = voiceRig();
    rig.session.speakSaved({
      synthesize: async () => MP3,
      onEnd: () => {},
      onError: () => {},
    });
    expect(rig.session.current().state).toBe("SPEAKING");
    rig.session.cancelSpeech();
    expect(rig.session.current().state).toBe("IDLE");
    expect(rig.adapter.log).toContain("stopSpeaking");
    expect(rig.session.current().state).not.toBe("STOPPED");
  });

  it("refuses a draft read while muted instead of claiming playback", () => {
    const rig = voiceRig();
    rig.session.setMuted(true);
    const onError = vi.fn();
    rig.session.speakSaved({
      synthesize: async () => MP3,
      onEnd: () => {},
      onError,
    });
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/muted/i));
    expect(rig.adapter.spoken).toEqual([]);
    expect(rig.session.current().state).toBe("MUTED");
  });

  it("reports an immediate start failure instead of a stuck speaking state", () => {
    const adapter = fakeAdapter({
      synthesisSupported: false,
      synthesisReason: "This browser cannot play spoken answers.",
    });
    const session = createVoiceSession({
      adapter,
      transcribe: async () => "unused",
      synthesize: async () => MP3,
      onState: () => {},
      onNotice: () => {},
      onTranscript: () => {},
    });
    const onError = vi.fn();
    session.speakSaved({
      synthesize: async () => MP3,
      onEnd: () => {},
      onError,
    });
    expect(onError).toHaveBeenCalledWith(
      "This browser cannot play spoken answers.",
    );
    expect(adapter.spoken).toEqual([]);
    expect(session.current().state).toBe("IDLE");
  });

  it("cancels draft playback on a global stop and mute", () => {
    for (const action of ["stop", "mute"] as const) {
      const rig = voiceRig();
      rig.session.speakSaved({
        synthesize: async () => MP3,
        onEnd: () => {},
        onError: () => {},
      });
      expect(rig.session.current().state).toBe("SPEAKING");
      if (action === "stop") rig.session.stop();
      else rig.session.setMuted(true);
      expect(rig.adapter.log).toContain("stopSpeaking");
      expect(rig.session.current().state).not.toBe("SPEAKING");
    }
  });

  it("reports unavailable spoken answers without changing voice state", () => {
    const adapter = fakeAdapter({
      synthesisSupported: false,
      synthesisReason: "This browser cannot play spoken answers.",
    });
    const notices: string[] = [];
    const session = createVoiceSession({
      adapter,
      transcribe: async () => "unused",
      synthesize: async () => MP3,
      onState: () => {},
      onNotice: (message) => notices.push(message),
      onTranscript: () => {},
    });
    session.speakManually(turn());
    expect(notices).toEqual(["This browser cannot play spoken answers."]);
    expect(session.current().state).toBe("IDLE");
  });

  it("treats a spoken yes as an ordinary turn with no approval authority", async () => {
    expect(voiceTurnRequest("  yes  ")).toEqual({
      modality: "VOICE",
      text: "yes",
      referents: [],
    });

    const rig = voiceRig();
    await rig.voiceTurn("yes");
    expect(rig.transcripts).toEqual(["yes"]);
    expect(rig.appended).toHaveLength(1);
    expect(rig.appended[0]?.modality).toBe("VOICE");
    // The session carries presentation state only, so a spoken "yes" cannot
    // become an approval: no approval field exists.
    expect(Object.keys(rig.session.current()).sort()).toEqual([
      "error",
      "microphoneSupported",
      "muted",
      "state",
      "transcript",
      "turn",
      "voiceMode",
    ]);
    expect(rig.notices).toEqual([]);
  });

  it("exposes the microphone, mute, stop and read-aloud controls", () => {
    const rig = voiceRig();
    expect(voiceControls(rig.session.current())).toEqual({
      listening: false,
      capturing: false,
      transcribing: false,
      speaking: false,
      muted: false,
      voiceModeEnabled: true,
      microphoneDisabled: false,
      stopAvailable: false,
      readAloudAvailable: true,
    });

    rig.startListening();
    expect(voiceControls(rig.session.current()).listening).toBe(true);
    expect(voiceControls(rig.session.current()).stopAvailable).toBe(true);

    rig.session.setMuted(true);
    expect(voiceControls(rig.session.current()).muted).toBe(true);
    expect(voiceControls(rig.session.current()).readAloudAvailable).toBe(false);
  });

  it("ignores a stopped microphone attempt's transcript, error and end", () => {
    const rig = voiceRig();
    rig.startListening();
    const stoppedAttempt = rig.adapter.capture[0]?.handlers;
    expect(stoppedAttempt).toBeDefined();

    rig.session.stop();
    expect(rig.session.current().state).toBe("STOPPED");

    stoppedAttempt?.onTranscript("late words");
    stoppedAttempt?.onError("Microphone permission was denied.");
    stoppedAttempt?.onEnd();
    expect(rig.transcripts).toEqual([]);
    expect(rig.appended).toEqual([]);
    // No stale notice, and no stale callback may move the session.
    expect(rig.notices).toEqual([]);
    expect(rig.session.current().state).toBe("STOPPED");
  });

  it("ignores the previous attempt's callbacks after a new listen", async () => {
    const rig = voiceRig();
    rig.startListening();
    const previous = rig.adapter.capture[0]?.handlers;
    expect(previous).toBeDefined();
    rig.session.stop();

    rig.startListening();
    expect(rig.session.current().state).toBe("LISTENING");

    previous?.onEnd();
    expect(rig.session.current().state).toBe("LISTENING");
    previous?.onTranscript("late words");
    expect(rig.transcripts).toEqual([]);
    previous?.onError("Microphone access was blocked.");
    expect(rig.notices).toEqual([]);
    expect(rig.session.current().state).toBe("LISTENING");

    // The live attempt still finishes normally.
    rig.adapter.deliverTranscript("What am I missing today?");
    await rig.settle();
    expect(rig.transcripts).toEqual(["What am I missing today?"]);
    expect(rig.appended).toHaveLength(1);
  });

  it("finishes the clip on the second microphone click instead of canceling it", async () => {
    const rig = voiceRig();
    rig.startListening();
    expect(rig.session.current().state).toBe("LISTENING");

    rig.finish();
    expect(rig.adapter.log).toContain("finishListening");
    expect(rig.adapter.log).not.toContain("stopListening");
    // The clip is uploading: the finish affordance is gone but Stop stays live.
    expect(rig.session.current().state).toBe("TRANSCRIBING");
    expect(voiceControls(rig.session.current())).toMatchObject({
      capturing: false,
      transcribing: true,
      listening: true,
      stopAvailable: true,
    });
    // A second microphone click cannot finish the same clip twice.
    rig.finish();
    expect(
      rig.adapter.log.filter((entry) => entry === "finishListening"),
    ).toHaveLength(1);

    rig.adapter.deliverTranscript("What am I missing today?");
    await rig.settle();
    expect(rig.transcripts).toEqual(["What am I missing today?"]);
    expect(rig.appended).toHaveLength(1);
    expect(rig.appended[0]?.modality).toBe("VOICE");
  });

  it("keeps listening when the recorder cannot start the upload", () => {
    const rig = voiceRig();
    rig.adapter.finishResult.value = false;
    rig.startListening();
    rig.finish();

    // The adapter still owns the microphone open, so the page must not claim
    // that a transcription is running.
    expect(rig.adapter.log).toContain("finishListening");
    expect(rig.session.current().state).toBe("LISTENING");
    expect(voiceControls(rig.session.current()).transcribing).toBe(false);
  });

  it("releases the session when an upload fails without a transcript", () => {
    const rig = voiceRig();
    rig.startListening();
    rig.finish();
    expect(rig.session.current().state).toBe("TRANSCRIBING");

    rig.adapter.failCapture("The recording could not be transcribed.");
    rig.adapter.endCapture();

    expect(rig.notices).toEqual(["The recording could not be transcribed."]);
    expect(rig.transcripts).toEqual([]);
    expect(rig.session.current().state).toBe("STOPPED");
    expect(voiceControls(rig.session.current()).stopAvailable).toBe(false);
  });

  it("forwards the session-scoped uploader to the capture attempt", () => {
    const rig = voiceRig();
    rig.startListening();
    expect(rig.adapter.capture).toHaveLength(1);
    expect(rig.adapter.capture[0]?.transcribe).toBe(rig.transcribe);
  });

  it("forwards the session-scoped saved-turn fetcher to playback", async () => {
    const rig = voiceRig();
    await rig.voiceTurn("What am I missing today?");
    // The adapter receives a selector and the page's fetcher, never answer text.
    expect(rig.adapter.synthesize).toBe(rig.synthesize);
    expect(rig.adapter.spoken).toEqual([TURN_ID]);
  });

  it("releases the session when the page refuses a delivered transcript", () => {
    const adapter = fakeAdapter();
    const session = createVoiceSession({
      adapter,
      transcribe: async () => "unused",
      synthesize: async () => MP3,
      onState: () => {},
      onNotice: () => {},
      // The page can refuse a transcript while another turn is in flight.
      onTranscript: () => {},
    });
    session.toggleListening();
    adapter.deliverTranscript("What am I missing today?");
    expect(session.current().state).toBe("STOPPED");
    expect(voiceControls(session.current()).stopAvailable).toBe(false);
  });

  it("aborts a finished clip's in-flight transcription on Stop", async () => {
    const rig = voiceRig();
    rig.startListening();
    rig.finish();

    rig.session.stop();
    expect(rig.adapter.log).toContain("stopListening");
    expect(rig.session.current().state).toBe("STOPPED");

    rig.adapter.deliverTranscript("late words");
    await rig.settle();
    expect(rig.transcripts).toEqual([]);
    expect(rig.appended).toEqual([]);
  });

  it("aborts capture and an in-flight upload on dispose with zero late turn", async () => {
    const rig = voiceRig();
    rig.startListening();
    rig.finish();

    rig.session.dispose();
    expect(rig.adapter.log).toContain("stopListening");

    rig.adapter.deliverTranscript("late words");
    await rig.settle();
    expect(rig.transcripts).toEqual([]);
    expect(rig.appended).toEqual([]);
  });

  it("keeps a stopped session stopped through mute and unmute", async () => {
    const gate = deferred<AssistantMessageResponse>();
    const rig = voiceRig(() => gate.promise);
    rig.startListening();
    rig.adapter.deliverTranscript("What am I missing today?");
    expect(rig.session.current().state).toBe("THINKING");

    rig.session.stop();
    rig.session.setMuted(true);
    expect(rig.session.current().state).toBe("STOPPED");
    expect(rig.session.current().muted).toBe(true);

    rig.session.setMuted(false);
    expect(rig.session.current().state).toBe("STOPPED");
    expect(rig.session.current().muted).toBe(false);

    gate.resolve({
      session_id: "session-a",
      turn: voiceTurn("VOICE", "What am I missing today?"),
      replay: false,
    });
    await rig.settle();
    expect(rig.adapter.spoken).toEqual([]);
    expect(rig.session.current().state).toBe("STOPPED");
  });

  it("plays an explicit read aloud after Stop and while the microphone is unsupported", async () => {
    const rig = voiceRig();
    await rig.voiceTurn("What am I missing today?");
    rig.session.stop();
    expect(rig.session.current().state).toBe("STOPPED");
    expect(voiceControls(rig.session.current()).stopAvailable).toBe(false);

    rig.session.speakManually(turn());
    expect(rig.session.current().state).toBe("SPEAKING");
    expect(rig.adapter.spoken).toEqual([TURN_ID, TURN_ID]);
    // The explicit control and the lifecycle now agree: Stop is live again.
    expect(voiceControls(rig.session.current()).stopAvailable).toBe(true);
    rig.session.stop();
    expect(rig.session.current().state).toBe("STOPPED");

    const adapter = fakeAdapter({
      captureSupported: false,
      captureReason: "This browser cannot record from the microphone.",
    });
    const session = createVoiceSession({
      adapter,
      transcribe: async () => "unused",
      synthesize: async () => MP3,
      onState: () => {},
      onNotice: () => {},
      onTranscript: () => {},
    });
    expect(session.current().state).toBe("UNSUPPORTED");
    session.speakManually(turn());
    expect(session.current().state).toBe("SPEAKING");
    expect(adapter.spoken).toEqual([TURN_ID]);
    expect(voiceControls(session.current())).toEqual({
      listening: false,
      capturing: false,
      transcribing: false,
      speaking: true,
      muted: false,
      voiceModeEnabled: true,
      microphoneDisabled: true,
      stopAvailable: true,
      readAloudAvailable: true,
    });
    adapter.finishSpeech();
    expect(session.current().state).toBe("UNSUPPORTED");
    expect(voiceControls(session.current()).microphoneDisabled).toBe(true);
    session.toggleListening();
    expect(adapter.log).not.toContain("startListening");

    // Mute still vetoes explicit playback.
    session.speakManually(turn());
    expect(session.current().state).toBe("SPEAKING");
    session.setMuted(true);
    expect(session.current().state).toBe("MUTED");
    session.speakManually(turn());
    expect(session.current().state).toBe("MUTED");
    expect(adapter.spoken).toEqual([TURN_ID, TURN_ID]);
  });

  it("releases the microphone when an explicit read aloud takes over", () => {
    const rig = voiceRig();
    rig.startListening();
    const attempt = rig.adapter.capture[0]?.handlers;
    expect(attempt).toBeDefined();

    rig.session.speakManually(turn());
    expect(rig.adapter.log).toContain("stopListening");
    expect(rig.session.current().state).toBe("SPEAKING");

    // The released attempt cannot end the playback that replaced it.
    attempt?.onEnd();
    attempt?.onError("Microphone access was blocked.");
    expect(rig.session.current().state).toBe("SPEAKING");
    expect(rig.notices).toEqual([]);
  });
});
