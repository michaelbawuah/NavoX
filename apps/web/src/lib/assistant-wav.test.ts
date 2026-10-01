import { describe, expect, it } from "vitest";
import {
  encodeWavPcm16,
  MAX_WAV_BYTES,
  MAX_WAV_MILLISECONDS,
  MIN_WAV_MILLISECONDS,
  parseAssistantWav,
  resampleTo16k,
  SpeechClipError,
  WAV_BITS_PER_SAMPLE,
  WAV_CHANNELS,
  WAV_SAMPLE_RATE,
} from "./assistant-wav";

function tone(samples: number, amplitude = 0.4): Float32Array {
  const buffer = new Float32Array(samples);
  for (let index = 0; index < samples; index += 1) {
    buffer[index] = Math.sin((index / 40) * Math.PI) * amplitude;
  }
  return buffer;
}

function int16At(bytes: Uint8Array, sample: number): number {
  return new DataView(
    bytes.buffer,
    bytes.byteOffset,
    bytes.byteLength,
  ).getInt16(44 + sample * 2, true);
}

describe("SPEC-005 WAV container", () => {
  it("writes mono 16 kHz 16-bit PCM bytes for a one-second clip", () => {
    const bytes = encodeWavPcm16(tone(WAV_SAMPLE_RATE), WAV_SAMPLE_RATE);
    expect(String.fromCharCode(...bytes.subarray(0, 4))).toBe("RIFF");
    expect(String.fromCharCode(...bytes.subarray(8, 12))).toBe("WAVE");
    expect(String.fromCharCode(...bytes.subarray(12, 16))).toBe("fmt ");
    const view = new DataView(bytes.buffer);
    expect(view.getUint16(20, true)).toBe(1); // uncompressed PCM
    expect(view.getUint16(22, true)).toBe(WAV_CHANNELS);
    expect(view.getUint32(24, true)).toBe(WAV_SAMPLE_RATE);
    expect(view.getUint16(34, true)).toBe(WAV_BITS_PER_SAMPLE);
    expect(view.getUint32(28, true)).toBe(WAV_SAMPLE_RATE * 2); // byte rate
    expect(view.getUint32(40, true)).toBe(WAV_SAMPLE_RATE * 2); // data size
    expect(bytes.byteLength).toBe(44 + WAV_SAMPLE_RATE * 2);
    expect(view.getUint32(4, true)).toBe(bytes.byteLength - 8);

    const info = parseAssistantWav(bytes);
    expect(info).toEqual({
      channels: 1,
      sampleRate: 16_000,
      bitsPerSample: 16,
      dataBytes: 32_000,
      durationMilliseconds: 1_000,
    });
  });

  it("clamps out-of-range samples instead of wrapping them", () => {
    const bytes = encodeWavPcm16(
      new Float32Array([0, 0.5, -0.5, 1, -1, 2, -2]),
      16_000,
    );
    expect(int16At(bytes, 0)).toBe(0);
    expect(Math.abs(int16At(bytes, 1) - 16_383)).toBeLessThanOrEqual(1);
    expect(Math.abs(int16At(bytes, 2) + 16_384)).toBeLessThanOrEqual(1);
    expect(int16At(bytes, 3)).toBe(32_767);
    expect(int16At(bytes, 4)).toBe(-32_768);
    expect(int16At(bytes, 5)).toBe(32_767);
    expect(int16At(bytes, 6)).toBe(-32_768);
  });

  it("resamples a 48 kHz device track to the fixed 16 kHz rate", () => {
    const device = tone(48_000);
    const resampled = resampleTo16k(device, 48_000);
    expect(resampled).toHaveLength(16_000);
    expect(resampleTo16k(device, 16_000)).toHaveLength(48_000);
    const bytes = encodeWavPcm16(device, 48_000);
    expect(parseAssistantWav(bytes).durationMilliseconds).toBe(1_000);
  });

  it("refuses a clip that is too short, too long or incomplete", () => {
    const short = encodeWavPcm16(tone(3_000), 16_000); // 187 ms
    expect(() => parseAssistantWav(short)).toThrow(SpeechClipError);
    expect(() => parseAssistantWav(short)).toThrow(/0\.2 and 30 seconds/);

    const long = encodeWavPcm16(tone(480_016), 16_000); // 30,001 ms
    expect(() => parseAssistantWav(long)).toThrow(SpeechClipError);

    const complete = encodeWavPcm16(tone(16_000), 16_000);
    expect(() => parseAssistantWav(complete.subarray(0, 1_000))).toThrow(
      /incomplete frame data/i,
    );
  });

  it("refuses bare PCM, a WebM container, and a mismatched rate", () => {
    const barePcm = new Uint8Array(32_000);
    expect(() => parseAssistantWav(barePcm)).toThrow(/uncompressed WAV/i);

    const webm = new Uint8Array(4_000);
    webm.set([0x1a, 0x45, 0xdf, 0xa3], 0);
    expect(() => parseAssistantWav(webm)).toThrow(/uncompressed WAV/i);

    const wrongRate = encodeWavPcm16(tone(16_000), 16_000);
    new DataView(wrongRate.buffer).setUint32(24, 8_000, true);
    expect(() => parseAssistantWav(wrongRate)).toThrow(/16 kHz mono 16-bit/i);

    const stereo = encodeWavPcm16(tone(16_000), 16_000);
    new DataView(stereo.buffer).setUint16(22, 2, true);
    expect(() => parseAssistantWav(stereo)).toThrow(/16 kHz mono 16-bit/i);

    // A compressed (µ-law) WAV looks like the right shape but is not PCM.
    const compressed = encodeWavPcm16(tone(16_000), 16_000);
    new DataView(compressed.buffer).setUint16(20, 7, true);
    expect(() => parseAssistantWav(compressed)).toThrow(/16 kHz mono 16-bit/i);
  });

  it("refuses an empty or oversized body before reading frames", () => {
    expect(() => parseAssistantWav(new Uint8Array(0))).toThrow(
      /record a question/i,
    );
    const oversized = new Uint8Array(MAX_WAV_BYTES + 1);
    oversized.set(
      [..."RIFF"].map((character) => character.charCodeAt(0)),
      0,
    );
    expect(() => parseAssistantWav(oversized)).toThrow(/too large/i);
  });

  it("bounds the accepted duration at both ends", () => {
    const shortest = Math.ceil((MIN_WAV_MILLISECONDS / 1000) * 16_000);
    expect(
      parseAssistantWav(encodeWavPcm16(tone(shortest), 16_000)),
    ).toMatchObject({ durationMilliseconds: MIN_WAV_MILLISECONDS });
    const longest = Math.floor((MAX_WAV_MILLISECONDS / 1000) * 16_000);
    expect(
      parseAssistantWav(encodeWavPcm16(tone(longest), 16_000)),
    ).toMatchObject({ durationMilliseconds: MAX_WAV_MILLISECONDS });
  });

  it("refuses an unusable device sample rate", () => {
    expect(() => resampleTo16k(tone(100), 0)).toThrow(SpeechClipError);
    expect(() => encodeWavPcm16(tone(100), Number.NaN)).toThrow(
      SpeechClipError,
    );
  });
});
