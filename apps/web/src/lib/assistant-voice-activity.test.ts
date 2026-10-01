import { describe, expect, it } from "vitest";
import { VoiceActivityDetector } from "./assistant-voice-activity";

const SAMPLE_RATE = 48_000;
function frame(amplitude: number): Float32Array {
  return new Float32Array(4_800).fill(amplitude);
}

describe("local speech activity", () => {
  it("starts after sustained voice and ends after silence", () => {
    const detector = new VoiceActivityDetector(false);
    expect(detector.observe(frame(0.01), SAMPLE_RATE).started).toBe(false);
    expect(detector.observe(frame(0.07), SAMPLE_RATE).started).toBe(false);
    expect(detector.observe(frame(0.07), SAMPLE_RATE).started).toBe(true);
    for (let index = 0; index < 8; index++) {
      expect(detector.observe(frame(0), SAMPLE_RATE).ended).toBe(false);
    }
    expect(detector.observe(frame(0), SAMPLE_RATE).ended).toBe(true);
    expect(detector.observe(frame(0.07), SAMPLE_RATE).started).toBe(false);
  });

  it("keeps speaker-level echo below the barge-in threshold", () => {
    const detector = new VoiceActivityDetector(true);
    for (let index = 0; index < 20; index++) {
      expect(detector.observe(frame(0.025), SAMPLE_RATE).started).toBe(false);
    }
    expect(detector.observe(frame(0.12), SAMPLE_RATE).started).toBe(false);
    expect(detector.observe(frame(0.12), SAMPLE_RATE).started).toBe(true);
  });

  it("does not treat invalid or empty audio as speech", () => {
    const detector = new VoiceActivityDetector(false);
    expect(detector.observe(new Float32Array(0), SAMPLE_RATE).started).toBe(
      false,
    );
    expect(detector.observe(frame(0.1), 0).started).toBe(false);
  });
});
