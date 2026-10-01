import { describe, expect, it } from "vitest";
import type { VoiceEvent, VoiceSessionState } from "./voice";
import {
  automaticSpeechAllowed,
  createInactiveWakeWordAdapter,
  initialVoiceState,
  reduceVoiceState,
} from "./voice";

function apply(
  state: VoiceSessionState,
  ...events: VoiceEvent[]
): VoiceSessionState {
  let next = state;
  for (const event of events) next = reduceVoiceState(next, event);
  return next;
}

describe("click-to-talk voice state", () => {
  it("walks listening to transcribed to thinking", () => {
    let state = initialVoiceState();
    state = reduceVoiceState(state, { type: "REQUEST_LISTENING" });
    expect(state.state).toBe("LISTENING");
    state = reduceVoiceState(state, {
      type: "TRANSCRIPT",
      text: " What am I missing today? ",
    });
    expect(state.state).toBe("TRANSCRIBING");
    expect(state.transcript).toBe("What am I missing today?");
    state = reduceVoiceState(state, { type: "SUBMITTED" });
    expect(state.state).toBe("THINKING");
    expect(state.turn).toBe(1);
    state = reduceVoiceState(state, { type: "ANSWER_READY" });
    expect(state.state).toBe("IDLE");
  });

  it("interrupts playback when a new utterance starts", () => {
    let state = reduceVoiceState(initialVoiceState(), {
      type: "SPEAKING_STARTED",
    });
    expect(state.state).toBe("SPEAKING");
    state = reduceVoiceState(state, { type: "BARGE_IN" });
    expect(state.state).toBe("LISTENING");
    expect(state.transcript).toBeNull();
  });

  it("stops speech and listening on an explicit stop", () => {
    const speaking = reduceVoiceState(initialVoiceState(), {
      type: "SPEAKING_STARTED",
    });
    expect(reduceVoiceState(speaking, { type: "STOP" }).state).toBe("STOPPED");
  });

  it("keeps an unsupported browser in the typed fallback", () => {
    const unsupported = reduceVoiceState(initialVoiceState(), {
      type: "MICROPHONE_UNSUPPORTED",
      reason: "No SpeechRecognition API.",
    });
    expect(unsupported.state).toBe("UNSUPPORTED");
    expect(unsupported.error).toBe("No SpeechRecognition API.");
    expect(
      reduceVoiceState(unsupported, { type: "REQUEST_LISTENING" }).state,
    ).toBe("UNSUPPORTED");
  });

  it("ignores an empty transcript instead of submitting silence", () => {
    const listening = reduceVoiceState(initialVoiceState(), {
      type: "REQUEST_LISTENING",
    });
    const state = reduceVoiceState(listening, {
      type: "TRANSCRIPT",
      text: "   ",
    });
    expect(state.state).toBe("IDLE");
    expect(state.transcript).toBeNull();
  });

  it("reports transcription errors without a transcript", () => {
    const state = reduceVoiceState(initialVoiceState(), {
      type: "TRANSCRIPT_FAILED",
      reason: "Microphone permission was denied.",
    });
    expect(state.state).toBe("IDLE");
    expect(state.transcript).toBeNull();
    expect(state.error).toBe("Microphone permission was denied.");
  });

  it("mutes an active answer and stays muted at rest", () => {
    const speaking = apply(initialVoiceState(), { type: "SPEAKING_STARTED" });
    expect(speaking.state).toBe("SPEAKING");
    const muted = apply(speaking, { type: "MUTE" });
    expect(muted.state).toBe("MUTED");
    expect(muted.muted).toBe(true);
    expect(automaticSpeechAllowed(muted)).toBe(false);
    expect(apply(muted, { type: "MUTE" })).toEqual(muted);
  });

  it("keeps a pending voice turn running while muted and stays silent", () => {
    const thinking = apply(
      initialVoiceState(),
      { type: "REQUEST_LISTENING" },
      { type: "TRANSCRIPT", text: "What am I missing today?" },
      { type: "SUBMITTED" },
      { type: "MUTE" },
    );
    expect(thinking.state).toBe("THINKING");
    expect(thinking.muted).toBe(true);
    expect(automaticSpeechAllowed(thinking)).toBe(false);
    // The answer is rendered visually and the session rests as MUTED.
    const answered = apply(thinking, { type: "ANSWER_READY" });
    expect(answered.state).toBe("MUTED");
    expect(answered.muted).toBe(true);
    expect(automaticSpeechAllowed(answered)).toBe(false);
  });

  it("returns to idle on unmute without reviving cancelled playback", () => {
    const muted = apply(initialVoiceState(), { type: "MUTE" });
    const unmuted = apply(muted, { type: "UNMUTE" });
    expect(unmuted.state).toBe("IDLE");
    expect(unmuted.muted).toBe(false);
    // A synthesis end event from the cancelled utterance changes nothing.
    expect(apply(unmuted, { type: "SPEAKING_ENDED" })).toEqual(unmuted);
    expect(automaticSpeechAllowed(unmuted)).toBe(true);
  });

  it("keeps an unsupported browser in the typed fallback while muted", () => {
    const unsupported = apply(initialVoiceState(), {
      type: "MICROPHONE_UNSUPPORTED",
      reason: "No SpeechRecognition API.",
    });
    expect(unsupported.microphoneSupported).toBe(false);
    const muted = apply(unsupported, { type: "MUTE" });
    expect(muted.state).toBe("UNSUPPORTED");
    expect(muted.muted).toBe(true);
    expect(apply(muted, { type: "UNMUTE" }).state).toBe("UNSUPPORTED");
    expect(automaticSpeechAllowed(muted)).toBe(false);
  });

  it("keeps Stop terminal through mute and unmute", () => {
    const stopped = apply(initialVoiceState(), { type: "STOP" });
    const muted = apply(stopped, { type: "MUTE" });
    expect(muted.state).toBe("STOPPED");
    expect(muted.muted).toBe(true);
    const unmuted = apply(muted, { type: "UNMUTE" });
    expect(unmuted.state).toBe("STOPPED");
    expect(unmuted.muted).toBe(false);
    // A stopped answer still cannot speak for itself after unmute.
    expect(automaticSpeechAllowed(unmuted)).toBe(false);
    expect(apply(unmuted, { type: "SPEAKING_STARTED" }).state).toBe("STOPPED");
  });

  it("plays an explicit read aloud after Stop and without a microphone", () => {
    const stopped = apply(initialVoiceState(), { type: "STOP" });
    const afterStop = apply(stopped, { type: "READ_ALOUD_STARTED" });
    expect(afterStop.state).toBe("SPEAKING");
    expect(apply(afterStop, { type: "SPEAKING_ENDED" }).state).toBe("IDLE");

    const unsupported = apply(initialVoiceState(), {
      type: "MICROPHONE_UNSUPPORTED",
      reason: "No SpeechRecognition API.",
    });
    // Only the microphone is missing: an answer still cannot speak for itself.
    expect(apply(unsupported, { type: "SPEAKING_STARTED" }).state).toBe(
      "UNSUPPORTED",
    );
    const readAloud = apply(unsupported, { type: "READ_ALOUD_STARTED" });
    expect(readAloud.state).toBe("SPEAKING");
    expect(readAloud.microphoneSupported).toBe(false);
    // Playback ends back in the typed fallback, not in a fake idle state.
    const rested = apply(readAloud, { type: "SPEAKING_ENDED" });
    expect(rested.state).toBe("UNSUPPORTED");
    expect(apply(rested, { type: "REQUEST_LISTENING" }).state).toBe(
      "UNSUPPORTED",
    );
    // Mute still wins over the explicit control.
    const muted = apply(readAloud, { type: "MUTE" });
    expect(muted.state).toBe("MUTED");
    expect(apply(muted, { type: "READ_ALOUD_STARTED" }).state).toBe("MUTED");
  });

  it("drops a transcript and a start confirmation after Stop", () => {
    const listening = apply(initialVoiceState(), { type: "REQUEST_LISTENING" });
    const stopped = apply(listening, { type: "STOP" });
    expect(stopped.state).toBe("STOPPED");
    const late = apply(
      stopped,
      { type: "TRANSCRIPT", text: "late words" },
      { type: "LISTENING_STARTED" },
      { type: "SPEAKING_STARTED" },
    );
    expect(late.state).toBe("STOPPED");
    expect(late.transcript).toBeNull();
  });

  it("lets the operator open the microphone again after Stop", () => {
    const stopped = apply(initialVoiceState(), { type: "STOP" });
    expect(apply(stopped, { type: "REQUEST_LISTENING" }).state).toBe(
      "LISTENING",
    );
  });

  it("holds no approval or action authority", () => {
    const state = apply(
      initialVoiceState(),
      { type: "REQUEST_LISTENING" },
      { type: "TRANSCRIPT", text: "yes" },
    );
    expect(state.transcript).toBe("yes");
    // The session state is exactly presentation and input provenance; a
    // spoken "yes" cannot become an approval because no such field exists.
    expect(Object.keys(state).sort()).toEqual([
      "error",
      "microphoneSupported",
      "muted",
      "state",
      "transcript",
      "turn",
    ]);
  });
});

describe("wake word boundary", () => {
  it("ships inert: unsupported, inactive and loud on start", async () => {
    const adapter = createInactiveWakeWordAdapter();
    expect(adapter.supported).toBe(false);
    expect(adapter.active).toBe(false);
    await expect(adapter.start(() => {})).rejects.toThrow(
      /not part of this release/i,
    );
    await expect(adapter.stop()).resolves.toBeUndefined();
    expect(adapter.active).toBe(false);
  });
});
