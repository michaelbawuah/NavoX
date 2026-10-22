import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

interface PcmMessage {
  type: string;
  id?: number;
  frame?: Float32Array;
}

interface Processor {
  port: {
    onmessage(event: { data: PcmMessage }): void;
    postMessage(message: PcmMessage): void;
  };
  process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean;
}

const source = readFileSync(
  new URL("../../public/audio/vox-pcm-processor-v1.js", import.meta.url),
  "utf8",
);

function bridge(sampleRate = 48_000) {
  const messages: PcmMessage[] = [];
  let Constructor!: new () => Processor;
  runInNewContext(source, {
    sampleRate,
    Float32Array,
    AudioWorkletProcessor: class {
      port = {
        onmessage: null,
        postMessage: vi.fn((message: PcmMessage) => messages.push(message)),
      };
    },
    registerProcessor(name: string, processor: new () => Processor) {
      expect(name).toBe("navox-vox-pcm-v1");
      Constructor = processor;
    },
  });
  const processor = new Constructor();
  const send = (message: PcmMessage) =>
    processor.port.onmessage({ data: message });
  return {
    processor,
    messages,
    send,
    frames: () => messages.filter((message) => message.type === "frame"),
    emit(amplitude = 0.25, count = 128) {
      const output = new Float32Array(count).fill(0.9);
      const running = processor.process(
        [[new Float32Array(count).fill(amplitude)]],
        [[output]],
      );
      return { running, output };
    },
  };
}

const silent = (frame: Float32Array) => frame.every((sample) => sample === 0);

describe("Vox AudioWorklet PCM bridge", () => {
  it("does not collect input or output audio before explicit enable", () => {
    const fake = bridge();
    for (let index = 0; index < 16; index += 1) {
      expect(silent(fake.emit().output)).toBe(true);
    }
    expect(fake.messages).toEqual([]);
  });

  it.each([8_000, 16_000, 44_100, 48_000, 96_000])(
    "bridges mono PCM without resampling at %i Hz and never copies raw input directly to output",
    (sampleRate) => {
      const fake = bridge(sampleRate);
      fake.send({ type: "enabled" });
      for (let index = 0; index < 8; index += 1) {
        expect(silent(fake.emit().output)).toBe(true);
      }
      const input = fake.frames()[0];
      expect(input.id).toBe(0);
      expect(input.frame).toHaveLength(1024);
      expect(input.frame?.every((value) => value === 0.25)).toBe(true);
      // The gate's returned frame can differ from the raw input. Only that
      // explicitly returned PCM is sent to the MediaStreamDestination.
      const approved = new Float32Array(1024).fill(0.125);
      fake.send({ type: "output", id: input.id, frame: approved });
      for (let index = 0; index < 8; index += 1) {
        expect(fake.emit(0).output.every((value) => value === 0.125)).toBe(
          true,
        );
      }
      expect(silent(approved)).toBe(true);
      expect(silent(fake.emit().output)).toBe(true);
    },
  );

  it("accepts varying render quantum sizes and sends complete bounded chunks", () => {
    const fake = bridge();
    fake.send({ type: "enabled" });
    for (const size of [128, 256, 512, 128]) {
      expect(silent(fake.emit(0.5, size).output)).toBe(true);
    }
    expect(fake.frames()).toHaveLength(1);
    expect(fake.frames()[0].frame).toHaveLength(1024);
  });

  it("fails closed after bounded main-thread delay and never retains an ongoing microphone queue", () => {
    const fake = bridge(16_000);
    fake.send({ type: "enabled" });
    let stoppedAt = 0;
    for (let index = 1; index < 100; index += 1) {
      const frame = fake.emit();
      expect(silent(frame.output)).toBe(true);
      if (!frame.running) {
        stoppedAt = index * 128;
        break;
      }
    }
    expect(stoppedAt).toBeGreaterThan(0);
    // The oldest complete 1024-sample input is retained for at most 350 ms.
    expect(stoppedAt).toBeLessThanOrEqual(1024 + 16_000 * 0.35 + 128);
    expect(fake.messages.at(-1)).toEqual({ type: "fault" });
    expect(fake.emit().running).toBe(false);
  });

  it("rejects out-of-order, duplicate, unsolicited and malformed gate outputs", () => {
    for (const invalid of [
      "reorder",
      "duplicate",
      "unsolicited",
      "nan",
      "size",
    ]) {
      const fake = bridge();
      fake.send({ type: "enabled" });
      if (invalid !== "unsolicited") {
        for (let index = 0; index < 16; index += 1) fake.emit();
      }
      if (invalid === "duplicate") {
        fake.send({ type: "output", id: 0, frame: new Float32Array(1024) });
      }
      fake.send({
        type: "output",
        id: invalid === "reorder" ? 1 : 0,
        frame: new Float32Array(invalid === "size" ? 128 : 1024).fill(
          invalid === "nan" ? Number.NaN : 0.75,
        ),
      });
      expect(fake.messages.at(-1)).toEqual({ type: "fault" });
      expect(fake.emit().running).toBe(false);
      expect(silent(fake.emit().output)).toBe(true);
    }
  });

  it("erases queued playback and ignores late messages after terminal stop", () => {
    const fake = bridge();
    fake.send({ type: "enabled" });
    for (let index = 0; index < 8; index += 1) fake.emit();
    const approved = new Float32Array(1024).fill(0.5);
    fake.send({ type: "output", id: 0, frame: approved });
    fake.send({ type: "stop" });
    expect(silent(approved)).toBe(true);
    fake.send({ type: "output", id: 1, frame: new Float32Array(1024).fill(1) });
    fake.send({ type: "enabled" });
    expect(silent(fake.emit().output)).toBe(true);
    expect(fake.emit().running).toBe(false);
  });

  it("fails silent for corrupt input and unsupported channel layouts", () => {
    for (const invalid of ["nan", "clipped", "stereo", "missing"]) {
      const fake = bridge();
      fake.send({ type: "enabled" });
      const input = new Float32Array(128).fill(
        invalid === "nan" ? Number.NaN : invalid === "clipped" ? 1.1 : 0.25,
      );
      const output = new Float32Array(128).fill(1);
      const channels =
        invalid === "missing"
          ? []
          : invalid === "stereo"
            ? [input, input]
            : [input];
      expect(fake.processor.process([channels], [[output]])).toBe(false);
      expect(silent(output)).toBe(true);
      expect(fake.messages.at(-1)).toEqual({ type: "fault" });
    }
  });
});
