import { describe, expect, it, vi } from "vitest";
import { createBrowserVoxAudioCaptureFactory } from "./vox-audio-capture";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function fakeTrack() {
  const events = new Map<string, Set<EventListenerOrEventListenerObject>>();
  const track = {
    enabled: true,
    readyState: "live",
    stop: vi.fn(() => {
      track.readyState = "ended";
    }),
    addEventListener: vi.fn(
      (type: string, listener: EventListenerOrEventListenerObject) => {
        const listeners = events.get(type) ?? new Set();
        listeners.add(listener);
        events.set(type, listeners);
      },
    ),
    removeEventListener: vi.fn(
      (type: string, listener: EventListenerOrEventListenerObject) =>
        events.get(type)?.delete(listener),
    ),
    end() {
      track.readyState = "ended";
      for (const listener of events.get("ended") ?? []) {
        if (typeof listener === "function") listener(new Event("ended"));
        else listener.handleEvent(new Event("ended"));
      }
    },
  };
  return track;
}

function stream(track: ReturnType<typeof fakeTrack>): MediaStream {
  return {
    getTracks: () => [track],
    getAudioTracks: () => [track],
  } as unknown as MediaStream;
}

function node() {
  return { connect: vi.fn(), disconnect: vi.fn() };
}

type ProcessEvent = { data: unknown };

function browser(
  options: {
    failure?: "source" | "processor" | "destination" | "connect" | "resume";
    suspended?: boolean;
    closingFails?: boolean;
    sampleRate?: number;
    opening?: Promise<MediaStream>;
    resuming?: Promise<void>;
    loading?: Promise<void>;
  } = {},
) {
  const raw = fakeTrack();
  const processed = fakeTrack();
  const source = node();
  const destination = { ...node(), stream: stream(processed) };
  const gains: {
    gain: { value: number };
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
  }[] = [];
  const processor = {
    ...node(),
    onprocessorerror: null as (() => void) | null,
    port: {
      onmessage: null as ((event: ProcessEvent) => void) | null,
      postMessage: vi.fn(),
      close: vi.fn(),
    },
  };
  const contexts: FakeContext[] = [];
  class FakeContext {
    sampleRate = options.sampleRate ?? 16_000;
    state = options.suspended ? "suspended" : "running";
    destination = { id: "hardware" };
    audioWorklet = {
      addModule: vi.fn(() => options.loading ?? Promise.resolve()),
    };
    constructor() {
      contexts.push(this);
    }
    createMediaStreamSource() {
      if (options.failure === "source") throw new Error("source");
      return source;
    }
    createMediaStreamDestination() {
      if (options.failure === "destination") throw new Error("destination");
      return destination;
    }
    createGain() {
      const gain = { ...node(), gain: { value: 1 } };
      gains.push(gain);
      return gain;
    }
    async resume() {
      if (options.failure === "resume") throw new Error("resume");
      await options.resuming;
    }
    async close() {
      if (options.closingFails) throw new Error("close");
    }
  }
  const close = vi.spyOn(FakeContext.prototype, "close");
  const resume = vi.spyOn(FakeContext.prototype, "resume");
  if (options.failure === "connect") {
    source.connect.mockImplementation(() => {
      throw new Error("connect");
    });
  }
  const getUserMedia = vi.fn(
    () => options.opening ?? Promise.resolve(stream(raw)),
  );
  class FakeWorklet {
    connect = processor.connect;
    disconnect = processor.disconnect;
    port = processor.port;
    get onprocessorerror() {
      return processor.onprocessorerror;
    }
    set onprocessorerror(value) {
      processor.onprocessorerror = value;
    }
    constructor() {
      if (options.failure === "processor") throw new Error("processor");
    }
  }
  let sequence = 0;
  const scope = {
    AudioWorkletNode: FakeWorklet,
    AudioContext: FakeContext,
    navigator: { mediaDevices: { getUserMedia } },
  };
  return {
    raw,
    processed,
    processor,
    gains,
    contexts,
    close,
    resume,
    source,
    destination,
    getUserMedia,
    scope,
    factory: createBrowserVoxAudioCaptureFactory({ qualified: true, scope }),
    emit(amplitude = 0, count = 1024, callback = processor.port.onmessage) {
      const input = new Float32Array(count).fill(amplitude);
      const id = sequence++;
      callback?.({ data: { type: "frame", id, frame: input } });
      const reply = processor.port.postMessage.mock.calls.find(
        ([message]) => message.type === "output" && message.id === id,
      );
      return reply?.[0].frame ?? new Float32Array(count);
    },
  };
}

function captureOptions(
  overrides: {
    signal?: AbortSignal;
    mayCapture?: () => boolean;
    onVoiceActivity?: (speech: boolean) => void | Promise<void>;
  } = {},
) {
  return {
    signal: new AbortController().signal,
    mayCapture: () => true,
    onVoiceActivity: vi.fn<(speech: boolean) => void>(),
    ...overrides,
  };
}

const silent = (frame: Float32Array) => frame.every((sample) => sample === 0);

describe("Vox local processed microphone capture", () => {
  it("is lazy, SSR-safe and unqualified by default, including direct create", async () => {
    const fake = browser();
    const factory = createBrowserVoxAudioCaptureFactory({ scope: fake.scope });
    expect(factory.qualified).toBe(false);
    expect(createBrowserVoxAudioCaptureFactory().qualified).toBe(false);
    expect(fake.getUserMedia).not.toHaveBeenCalled();
    await expect(factory.create(captureOptions())).rejects.toThrow(
      "not qualified",
    );
    expect(fake.getUserMedia).not.toHaveBeenCalled();
    expect(fake.contexts).toHaveLength(0);
  });

  it("fails closed for unsupported Web Audio even with an operator qualification", async () => {
    const factory = createBrowserVoxAudioCaptureFactory({
      qualified: true,
      scope: {},
    });
    expect(factory.qualified).toBe(false);
    await expect(factory.create(captureOptions())).rejects.toThrow(
      "not qualified",
    );
  });

  it("checks current permission before requesting a microphone", async () => {
    const fake = browser();
    await expect(
      fake.factory.create(captureOptions({ mayCapture: () => false })),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fake.getUserMedia).not.toHaveBeenCalled();
  });

  it("publishes a distinct processed track initially disabled and never routes raw input to it", async () => {
    const fake = browser();
    const options = captureOptions();
    const capture = await fake.factory.create(options);
    expect(capture.track).toBe(fake.processed);
    expect(capture.track).not.toBe(fake.raw);
    expect(fake.raw.enabled).toBe(false);
    expect(fake.processed.enabled).toBe(false);
    expect(fake.source.connect).toHaveBeenCalledWith(
      expect.objectContaining({ port: fake.processor.port }),
    );
    expect(fake.processor.connect).toHaveBeenCalledWith(fake.gains[0]);
    expect(fake.gains[0].connect).toHaveBeenCalledWith(fake.destination);
    expect(fake.gains.map((gain) => gain.gain.value)).toEqual([0, 0]);
    expect(options.onVoiceActivity).toHaveBeenCalledWith(false);
    expect(silent(fake.emit(0.3))).toBe(true);
    capture.stop();
  });

  it("sends zero quiet frames and keeps bounded first-syllable pre-roll until speech starts", async () => {
    const fake = browser();
    const options = captureOptions();
    const capture = await fake.factory.create(options);
    capture.setEnabled(true);
    for (let index = 0; index < 100; index += 1)
      expect(silent(fake.emit())).toBe(true);
    expect(options.onVoiceActivity).toHaveBeenCalledTimes(1);
    expect(silent(fake.emit(0.1))).toBe(true);
    const first = fake.emit(0.2);
    expect(first[0]).toBeCloseTo(0.1);
    expect(options.onVoiceActivity).toHaveBeenLastCalledWith(true);
    expect(fake.emit()[0]).toBeCloseTo(0.2);
    expect(options.onVoiceActivity).toHaveBeenLastCalledWith(true);
    expect(silent(fake.emit())).toBe(true);
    expect(options.onVoiceActivity).toHaveBeenLastCalledWith(true);
    for (let index = 0; index < 14; index += 1)
      expect(silent(fake.emit())).toBe(true);
    expect(options.onVoiceActivity).toHaveBeenLastCalledWith(false);
    capture.stop();
  });

  it("keeps one speech window through short gaps and stops after sustained quiet", async () => {
    const fake = browser();
    const options = captureOptions();
    const capture = await fake.factory.create(options);
    capture.setEnabled(true);
    fake.emit(0.1);
    fake.emit(0.2);
    for (let index = 0; index < 6; index += 1) fake.emit();
    expect(vi.mocked(options.onVoiceActivity).mock.calls).toEqual([
      [false],
      [true],
    ]);
    fake.emit(0.3);
    fake.emit(0.2);
    for (let index = 0; index < 16; index += 1) fake.emit();
    expect(vi.mocked(options.onVoiceActivity).mock.calls).toEqual([
      [false],
      [true],
      [false],
    ]);
    expect(fake.raw.stop).not.toHaveBeenCalled();
    capture.stop();
  });

  it("holds voiced pre-roll until upstream acknowledges and checks permission again", async () => {
    const fake = browser();
    const ack = deferred<void>();
    let allowed = true;
    const options = captureOptions({
      mayCapture: () => allowed,
      onVoiceActivity: (speech) => (speech ? ack.promise : undefined),
    });
    const capture = await fake.factory.create(options);
    capture.setEnabled(true);
    fake.emit(0.1);
    expect(silent(fake.emit(0.2))).toBe(true);
    allowed = false;
    ack.resolve();
    await Promise.resolve();
    expect(silent(fake.emit(0.3))).toBe(true);
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.processed.stop).toHaveBeenCalledTimes(1);
    expect(fake.gains[0].gain.value).toBe(0);
  });

  it("retains the initial speech until an authorized acknowledgment resolves", async () => {
    const fake = browser();
    const ack = deferred<void>();
    const capture = await fake.factory.create(
      captureOptions({
        onVoiceActivity: (speech) => (speech ? ack.promise : undefined),
      }),
    );
    capture.setEnabled(true);
    fake.emit(0.1);
    expect(silent(fake.emit(0.2))).toBe(true);
    ack.resolve();
    await Promise.resolve();
    expect(fake.emit()[0]).toBeCloseTo(0.1);
    expect(fake.emit()[0]).toBeCloseTo(0.2);
    expect(silent(fake.emit())).toBe(true);
    capture.stop();
  });

  it("bounds stalled voiced or quiet acknowledgment buffers and stops all capture", async () => {
    for (const amplitude of [0, 0.3]) {
      const fake = browser();
      const capture = await fake.factory.create(
        captureOptions({
          onVoiceActivity: (speech) =>
            speech ? new Promise<void>(() => {}) : undefined,
        }),
      );
      capture.setEnabled(true);
      fake.emit(0.1);
      fake.emit(0.2);
      for (let index = 0; index < 12; index += 1)
        expect(silent(fake.emit(amplitude))).toBe(true);
      expect(fake.raw.stop).toHaveBeenCalledTimes(1);
      expect(fake.processed.stop).toHaveBeenCalledTimes(1);
      expect(fake.gains[0].gain.value).toBe(0);
    }
  });

  it("mute is a synchronous terminal gate, including a dequeued old processor callback", async () => {
    const fake = browser();
    const options = captureOptions();
    const capture = await fake.factory.create(options);
    capture.setEnabled(true);
    fake.emit(0.1);
    fake.emit(0.2);
    const stale = fake.processor.port.onmessage;
    capture.setEnabled(false);
    expect(fake.gains[0].gain.value).toBe(0);
    expect(fake.processed.enabled).toBe(false);
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.processed.stop).toHaveBeenCalledTimes(1);
    expect(silent(fake.emit(0.8, 1024, stale))).toBe(true);
    expect(() => capture.setEnabled(true)).toThrow("no longer permitted");
    capture.stop();
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
  });

  it("revokes raw and processed audio at the first frame when the owner lease disappears", async () => {
    const fake = browser();
    let allowed = true;
    const capture = await fake.factory.create(
      captureOptions({ mayCapture: () => allowed }),
    );
    capture.setEnabled(true);
    fake.emit(0.1);
    fake.emit(0.2);
    allowed = false;
    expect(silent(fake.emit(0.4))).toBe(true);
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.processed.enabled).toBe(false);
    expect(fake.gains[0].gain.value).toBe(0);
  });

  it("abort rejects immediately and releases microphone tracks arriving after a permission prompt", async () => {
    const opening = deferred<MediaStream>();
    const fake = browser({ opening: opening.promise });
    const controller = new AbortController();
    const pending = fake.factory.create(
      captureOptions({ signal: controller.signal }),
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    opening.resolve(stream(fake.raw));
    await Promise.resolve();
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.contexts).toHaveLength(0);
  });

  it("releases tracks if abort lands between permission resolution and the create continuation", async () => {
    const opening = deferred<MediaStream>();
    const fake = browser({ opening: opening.promise });
    const controller = new AbortController();
    const pending = fake.factory.create(
      captureOptions({ signal: controller.signal }),
    );
    opening.resolve(stream(fake.raw));
    queueMicrotask(() => controller.abort());
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.contexts).toHaveLength(0);
  });

  it("abort synchronously silences and releases an established capture graph", async () => {
    const fake = browser({ closingFails: true });
    const controller = new AbortController();
    const capture = await fake.factory.create(
      captureOptions({ signal: controller.signal }),
    );
    capture.setEnabled(true);
    controller.abort();
    expect(fake.gains[0].gain.value).toBe(0);
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.processed.stop).toHaveBeenCalledTimes(1);
    expect(fake.processor.port.onmessage).toBeNull();
    expect(fake.source.disconnect).toHaveBeenCalledTimes(1);
    expect(fake.processor.disconnect).toHaveBeenCalledTimes(1);
    expect(fake.destination.disconnect).toHaveBeenCalledTimes(1);
    await Promise.resolve();
  });

  it("aborts a pending context resume and never reopens output when it settles late", async () => {
    const resuming = deferred<void>();
    const fake = browser({ resuming: resuming.promise });
    const controller = new AbortController();
    const pending = fake.factory.create(
      captureOptions({ signal: controller.signal }),
    );
    await vi.waitFor(() => expect(fake.resume).toHaveBeenCalledTimes(1));
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    resuming.resolve();
    await Promise.resolve();
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.processed.stop).toHaveBeenCalledTimes(1);
    expect(fake.gains[0].gain.value).toBe(0);
    expect(fake.processor.port.onmessage).toBeNull();
  });

  it("aborts a pending same-origin worklet module load before constructing or connecting nodes", async () => {
    const loading = deferred<void>();
    const fake = browser({ loading: loading.promise });
    const controller = new AbortController();
    const pending = fake.factory.create(
      captureOptions({ signal: controller.signal }),
    );
    await vi.waitFor(() => expect(fake.contexts).toHaveLength(1));
    expect(fake.contexts[0].audioWorklet.addModule).toHaveBeenCalledWith(
      "/audio/vox-pcm-processor-v1.js",
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    loading.resolve();
    await Promise.resolve();
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.close).toHaveBeenCalledTimes(1);
    expect(fake.source.connect).not.toHaveBeenCalled();
    expect(fake.processed.enabled).toBe(true);
    expect(fake.processed.stop).not.toHaveBeenCalled();
  });

  it("releases the microphone if loading the worklet module fails", async () => {
    const fake = browser({
      loading: Promise.reject(new Error("module refused")),
    });
    await expect(fake.factory.create(captureOptions())).rejects.toThrow(
      "module refused",
    );
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.close).toHaveBeenCalledTimes(1);
    expect(fake.source.connect).not.toHaveBeenCalled();
  });

  it("stops both tracks on processor errors, worklet faults, malformed or replayed frames", async () => {
    for (const failure of ["processor", "fault", "missing", "replay"]) {
      const fake = browser();
      const capture = await fake.factory.create(captureOptions());
      capture.setEnabled(true);
      if (failure === "processor") fake.processor.onprocessorerror?.();
      else if (failure === "fault")
        fake.processor.port.onmessage?.({ data: { type: "fault" } });
      else if (failure === "missing")
        fake.processor.port.onmessage?.({ data: null });
      else {
        fake.emit();
        fake.processor.port.onmessage?.({
          data: { type: "frame", id: 0, frame: new Float32Array(1024) },
        });
      }
      expect(fake.raw.stop).toHaveBeenCalledTimes(1);
      expect(fake.processed.stop).toHaveBeenCalledTimes(1);
      expect(fake.processor.port.close).toHaveBeenCalledTimes(1);
      expect(fake.processor.port.onmessage).toBeNull();
      expect(fake.processor.onprocessorerror).toBeNull();
      expect(fake.gains[0].gain.value).toBe(0);
    }
  });

  it("continues physical teardown when the worklet port has already failed", async () => {
    const fake = browser();
    const capture = await fake.factory.create(captureOptions());
    capture.setEnabled(true);
    fake.processor.port.postMessage.mockImplementation(() => {
      throw new Error("port unavailable");
    });
    fake.processor.port.close.mockImplementation(() => {
      throw new Error("port already closed");
    });
    expect(() => capture.stop()).not.toThrow();
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.processed.stop).toHaveBeenCalledTimes(1);
    expect(fake.source.disconnect).toHaveBeenCalledTimes(1);
    expect(fake.processor.disconnect).toHaveBeenCalledTimes(1);
    expect(fake.close).toHaveBeenCalledTimes(1);
  });

  it("stops the whole graph when the hardware track ends and removes track listeners", async () => {
    const fake = browser();
    const capture = await fake.factory.create(captureOptions());
    capture.setEnabled(true);
    fake.raw.end();
    expect(fake.processed.stop).toHaveBeenCalledTimes(1);
    expect(fake.raw.removeEventListener).toHaveBeenCalled();
    expect(fake.processed.removeEventListener).toHaveBeenCalled();
    expect(fake.gains[0].gain.value).toBe(0);
    expect(silent(fake.emit(0.5))).toBe(true);
  });

  it("handles repeated speech locally after quiet without opening another device", async () => {
    const fake = browser();
    const activity = vi.fn<(speech: boolean) => void>();
    const options = captureOptions({ onVoiceActivity: activity });
    const capture = await fake.factory.create(options);
    capture.setEnabled(true);
    for (let utterance = 0; utterance < 3; utterance += 1) {
      expect(silent(fake.emit(0.1))).toBe(true);
      expect(fake.emit(0.2)[0]).toBeCloseTo(0.1);
      expect(fake.emit()[0]).toBeCloseTo(0.2);
      for (let index = 0; index < 16; index += 1)
        expect(silent(fake.emit())).toBe(true);
    }
    expect(activity.mock.calls.map(([speech]) => speech)).toEqual([
      false,
      true,
      false,
      true,
      false,
      true,
      false,
    ]);
    expect(fake.getUserMedia).toHaveBeenCalledTimes(1);
    capture.stop();
  });

  it("fails closed when a speech acknowledgment rejects or revokes capture synchronously", async () => {
    for (const reject of [false, true]) {
      const fake = browser();
      let allowed = true;
      const capture = await fake.factory.create(
        captureOptions({
          mayCapture: () => allowed,
          onVoiceActivity: (speech) => {
            if (!speech) return;
            if (reject) return Promise.reject(new Error("resume rejected"));
            allowed = false;
          },
        }),
      );
      capture.setEnabled(true);
      fake.emit(0.1);
      expect(silent(fake.emit(0.2))).toBe(true);
      await Promise.resolve();
      expect(fake.raw.stop).toHaveBeenCalledTimes(1);
      expect(fake.processed.stop).toHaveBeenCalledTimes(1);
      expect(fake.gains[0].gain.value).toBe(0);
    }
  });

  it("cleans up if a worklet message getter throws and if enabling a track throws", async () => {
    const fake = browser();
    const capture = await fake.factory.create(captureOptions());
    capture.setEnabled(true);
    fake.processor.port.onmessage?.({
      get data() {
        throw new Error("buffer unavailable");
      },
    });
    expect(fake.raw.stop).toHaveBeenCalledTimes(1);
    expect(fake.processed.stop).toHaveBeenCalledTimes(1);

    const broken = browser();
    const fresh = await broken.factory.create(captureOptions());
    Object.defineProperty(broken.raw, "enabled", {
      set() {
        throw new Error("track unavailable");
      },
    });
    expect(() => fresh.setEnabled(true)).toThrow("no longer permitted");
    expect(broken.raw.stop).toHaveBeenCalledTimes(1);
    expect(broken.processed.stop).toHaveBeenCalledTimes(1);
    expect(broken.gains[0].gain.value).toBe(0);
  });

  it.each(["source", "processor", "destination", "connect", "resume"] as const)(
    "releases every acquired resource when the %s node fails",
    async (failure) => {
      const fake = browser({ failure });
      await expect(fake.factory.create(captureOptions())).rejects.toThrow(
        failure,
      );
      expect(fake.raw.stop).toHaveBeenCalledTimes(1);
      if (failure === "connect" || failure === "resume")
        expect(fake.processed.stop).toHaveBeenCalledTimes(1);
      expect(fake.close).toHaveBeenCalledTimes(1);
    },
  );

  it("fails closed if AudioContext stays suspended after resume or its rate is invalid", async () => {
    for (const settings of [{ suspended: true }, { sampleRate: Number.NaN }]) {
      const fake = browser(settings);
      await expect(fake.factory.create(captureOptions())).rejects.toThrow();
      expect(fake.raw.stop).toHaveBeenCalledTimes(1);
      expect(fake.close).toHaveBeenCalledTimes(1);
    }
  });

  it("silences invalid frames and cleans up instead of retaining malformed PCM", async () => {
    for (const [amplitude, count] of [
      [Number.NaN, 1024],
      [0.2, 2048],
    ]) {
      const fake = browser();
      const capture = await fake.factory.create(captureOptions());
      capture.setEnabled(true);
      expect(silent(fake.emit(amplitude, count))).toBe(true);
      expect(fake.raw.stop).toHaveBeenCalledTimes(1);
      expect(fake.processed.stop).toHaveBeenCalledTimes(1);
    }
  });
});
