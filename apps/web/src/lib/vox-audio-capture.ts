import { VoiceActivityDetector } from "./assistant-voice-activity";
import type { VoxAudioCapture, VoxAudioCaptureFactory } from "./vox-livekit";

interface AudioNodeLike {
  connect(node: unknown): void;
  disconnect(): void;
}

interface ProcessorLike extends AudioNodeLike {
  onaudioprocess:
    | ((event: {
        inputBuffer: { getChannelData(channel: number): Float32Array };
        outputBuffer: { getChannelData(channel: number): Float32Array };
      }) => void)
    | null;
}

interface GainLike extends AudioNodeLike {
  gain: { value: number };
}

interface ContextLike {
  sampleRate: number;
  state?: string;
  destination: unknown;
  createMediaStreamSource(stream: MediaStream): AudioNodeLike;
  createScriptProcessor(
    size: number,
    input: number,
    output: number,
  ): ProcessorLike;
  createMediaStreamDestination(): AudioNodeLike & { stream: MediaStream };
  createGain(): GainLike;
  resume(): Promise<void> | void;
  close(): Promise<void> | void;
}

interface CaptureScope {
  navigator?: {
    mediaDevices?: {
      getUserMedia(constraints: MediaStreamConstraints): Promise<MediaStream>;
    };
  };
  AudioContext?: new () => ContextLike;
  webkitAudioContext?: new () => ContextLike;
}

const FRAME_SAMPLES = 1024;
const BUFFER_SECONDS = 0.35;
const VOICED_RMS = 0.025;

function aborted(): Error {
  const error = new Error("Vox microphone capture is no longer permitted.");
  error.name = "AbortError";
  return error;
}

function stopStream(stream: MediaStream | null) {
  for (const track of stream?.getTracks() ?? []) {
    try {
      track.enabled = false;
    } catch {
      // Track.stop is an independent, stronger release below.
    }
    try {
      track.stop();
    } catch {
      // Continue releasing every other owned track.
    }
  }
}

function disconnect(node: AudioNodeLike | null) {
  try {
    node?.disconnect();
  } catch {
    // Tracks and the context are independently released below.
  }
}

function silence(gain: GainLike | null) {
  try {
    if (gain) gain.gain.value = 0;
  } catch {
    // Disabling/stopping tracks and disconnecting the graph are independent.
  }
}

function abortable<T>(
  pending: Promise<T>,
  signal: AbortSignal,
  late: (value: T) => void = () => {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let cancelled = signal.aborted;
    const cancel = () => {
      cancelled = true;
      reject(aborted());
    };
    if (cancelled) reject(aborted());
    else signal.addEventListener("abort", cancel, { once: true });
    void pending.then(
      (value) => {
        signal.removeEventListener("abort", cancel);
        if (cancelled || signal.aborted) late(value);
        else resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", cancel);
        if (!cancelled) reject(error);
      },
    );
  });
}

/**
 * Opt-in local VAD and processed audio, never a raw microphone publication.
 * ScriptProcessor is a deprecated compatibility path: this implementation is
 * unqualified by default, and source tests do not establish device latency,
 * background behavior, echo rejection, or autoplay support. No I/O occurs until
 * an admitted adapter explicitly calls create after its room/agent are ready.
 */
export function createBrowserVoxAudioCaptureFactory(
  options: { qualified?: boolean; scope?: unknown } = {},
): VoxAudioCaptureFactory {
  const environment = (options.scope ??
    (typeof window === "undefined" ? {} : window)) as CaptureScope;
  const devices = environment.navigator?.mediaDevices;
  const Context = environment.AudioContext ?? environment.webkitAudioContext;
  const methods = Context?.prototype;
  const supported =
    typeof devices?.getUserMedia === "function" &&
    typeof Context === "function" &&
    [
      methods?.createMediaStreamSource,
      methods?.createScriptProcessor,
      methods?.createMediaStreamDestination,
      methods?.createGain,
      methods?.resume,
      methods?.close,
    ].every((method) => typeof method === "function");
  const qualified = options.qualified === true && supported;

  return {
    qualified,
    async create({ signal, mayCapture, onVoiceActivity }) {
      if (!qualified || !Context || !devices) {
        throw new Error(
          "Vox microphone capture is not qualified on this device.",
        );
      }
      let stopped = false;
      let enabled = false;
      let raw: MediaStream | null = null;
      let output: MediaStream | null = null;
      let track: MediaStreamTrack | null = null;
      let context: ContextLike | null = null;
      let source: AudioNodeLike | null = null;
      let processor: ProcessorLike | null = null;
      let master: GainLike | null = null;
      let silentSink: GainLike | null = null;
      let destination: (AudioNodeLike & { stream: MediaStream }) | null = null;
      let detector = new VoiceActivityDetector(false);
      let queue: Float32Array[] = [];
      let buffered = 0;
      let offset = 0;
      let speaking = false;
      let acknowledged = false;
      let waitingSamples = 0;
      let voiceEpoch = 0;
      const releaseTrackEvents: (() => void)[] = [];

      const permitted = () => {
        if (stopped || signal.aborted) return false;
        try {
          return mayCapture();
        } catch {
          return false;
        }
      };
      const clearFrames = () => {
        for (const frame of queue) frame.fill(0);
        queue = [];
        buffered = offset = 0;
      };
      const notifyQuiet = () => {
        speaking = acknowledged = false;
        waitingSamples = 0;
        voiceEpoch += 1;
        try {
          void Promise.resolve(onVoiceActivity(false)).catch(() => stop());
        } catch {
          stop();
        }
      };
      const stop = () => {
        if (stopped) return;
        stopped = true;
        enabled = false;
        voiceEpoch += 1;
        silence(master);
        silence(silentSink);
        try {
          if (track) track.enabled = false;
        } catch {
          // Its stream tracks are independently stopped below.
        }
        stopStream(raw);
        stopStream(output);
        for (const release of releaseTrackEvents.splice(0)) {
          try {
            release();
          } catch {
            // Continue disconnecting every node even if an ended listener fails.
          }
        }
        clearFrames();
        if (processor) processor.onaudioprocess = null;
        for (const node of [
          source,
          processor,
          master,
          silentSink,
          destination,
        ]) {
          disconnect(node);
        }
        signal.removeEventListener("abort", stop);
        if (speaking) notifyQuiet();
        try {
          void Promise.resolve(context?.close()).catch(() => {});
        } catch {
          // Tracks and graph edges have already been synchronously released.
        }
      };

      if (!permitted()) throw aborted();
      signal.addEventListener("abort", stop, { once: true });
      try {
        const acquired = await abortable(
          devices.getUserMedia({
            video: false,
            audio: {
              channelCount: 1,
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true,
            },
          }),
          signal,
          stopStream,
        );
        if (!permitted()) {
          stopStream(acquired);
          throw aborted();
        }
        raw = acquired;
        for (const rawTrack of raw.getTracks()) rawTrack.enabled = false;
        if (
          raw.getAudioTracks().length !== 1 ||
          raw.getAudioTracks()[0].readyState === "ended"
        ) {
          throw new Error("Vox requires one microphone audio track.");
        }
        const watchTrack = (watched: MediaStreamTrack) => {
          watched.addEventListener("ended", stop, { once: true });
          releaseTrackEvents.push(() =>
            watched.removeEventListener("ended", stop),
          );
        };
        watchTrack(raw.getAudioTracks()[0]);
        context = new Context();
        if (
          !Number.isFinite(context.sampleRate) ||
          context.sampleRate < 8000 ||
          context.sampleRate > 96_000
        ) {
          throw new Error("Vox microphone sample rate is unsupported.");
        }
        source = context.createMediaStreamSource(raw);
        processor = context.createScriptProcessor(FRAME_SAMPLES, 1, 1);
        master = context.createGain();
        master.gain.value = 0;
        silentSink = context.createGain();
        silentSink.gain.value = 0;
        destination = context.createMediaStreamDestination();
        output = destination.stream;
        track = output.getAudioTracks()[0] ?? null;
        if (!track || output.getAudioTracks().length !== 1) {
          throw new Error("Vox processed microphone output is unavailable.");
        }
        track.enabled = false;
        watchTrack(track);
        const sampleRate = context.sampleRate;
        const maxBuffered = Math.ceil(sampleRate * BUFFER_SECONDS);

        const startSpeech = () => {
          speaking = true;
          acknowledged = false;
          waitingSamples = 0;
          const epoch = ++voiceEpoch;
          try {
            const acknowledgment = onVoiceActivity(true);
            if (!permitted() || !enabled) {
              stop();
              return;
            }
            if (acknowledgment) {
              void acknowledgment.then(
                () => {
                  if (epoch !== voiceEpoch || !permitted() || !enabled) return;
                  acknowledged = true;
                  waitingSamples = 0;
                },
                () => stop(),
              );
            } else acknowledged = true;
          } catch {
            stop();
          }
        };

        processor.onaudioprocess = (event) => {
          let out: Float32Array | null = null;
          try {
            out = event.outputBuffer.getChannelData(0);
            out.fill(0);
            if (stopped || !enabled) return;
            if (!permitted()) {
              stop();
              return;
            }
            const frame = event.inputBuffer.getChannelData(0);
            if (
              frame.length !== out.length ||
              frame.length === 0 ||
              frame.length > FRAME_SAMPLES
            ) {
              stop();
              return;
            }
            let energy = 0;
            for (const sample of frame) {
              if (!Number.isFinite(sample) || Math.abs(sample) > 1) {
                stop();
                return;
              }
              energy += sample * sample;
            }
            const voiced = Math.sqrt(energy / frame.length) >= VOICED_RMS;
            const activity = detector.observe(frame, sampleRate);
            if (activity.ended) detector = new VoiceActivityDetector(false);
            if (voiced) {
              queue.push(Float32Array.from(frame));
              buffered += frame.length;
              if (!speaking && !activity.heardSpeech) {
                while (buffered > maxBuffered && queue.length > 1) {
                  const removed = queue.shift();
                  if (removed) {
                    buffered -= removed.length;
                    removed.fill(0);
                  }
                }
              } else if (buffered > maxBuffered) {
                // A stalled upstream acknowledgment must not retain a call.
                stop();
                return;
              }
            } else if (!speaking) clearFrames();

            if (!speaking && voiced && activity.heardSpeech) startSpeech();
            if (!permitted() || !enabled) {
              out.fill(0);
              stop();
              return;
            }
            if (!speaking) return;
            if (!acknowledged) {
              waitingSamples += frame.length;
              if (waitingSamples > maxBuffered) stop();
              return;
            }
            let written = 0;
            while (written < out.length && queue.length) {
              const next = queue[0];
              const count = Math.min(
                out.length - written,
                next.length - offset,
              );
              out.set(next.subarray(offset, offset + count), written);
              written += count;
              offset += count;
              buffered -= count;
              if (offset === next.length) {
                next.fill(0);
                queue.shift();
                offset = 0;
              }
            }
            // Announce quiet on the following zero frame, after the voiced tail
            // has actually reached the output, rather than disabling its track.
            if (!voiced && written === 0) notifyQuiet();
          } catch {
            out?.fill(0);
            stop();
          }
        };
        source.connect(processor);
        processor.connect(master);
        master.connect(destination);
        // Some ScriptProcessor implementations pull only toward the hardware
        // destination. This independent sink is permanently silent.
        processor.connect(silentSink);
        silentSink.connect(context.destination);
        await abortable(Promise.resolve(context.resume()), signal);
        if (!permitted()) throw aborted();
        if (context.state && context.state !== "running") {
          throw new Error("Vox microphone audio could not start.");
        }
        notifyQuiet();
        if (!permitted()) throw aborted();
        const processedTrack = track;
        const capture: VoxAudioCapture = {
          track: processedTrack,
          setEnabled(value) {
            if (!value) {
              // Muting is terminal for this object; the adapter must recreate
              // capture on resume instead of retaining a muted raw microphone.
              stop();
              return;
            }
            if (
              !permitted() ||
              processedTrack.readyState === "ended" ||
              raw
                ?.getAudioTracks()
                .some((rawTrack) => rawTrack.readyState === "ended")
            ) {
              stop();
              throw aborted();
            }
            try {
              enabled = true;
              for (const rawTrack of raw?.getAudioTracks() ?? []) {
                rawTrack.enabled = true;
              }
              if (master) master.gain.value = 1;
              processedTrack.enabled = true;
            } catch {
              stop();
              throw aborted();
            }
          },
          stop,
        };
        return capture;
      } catch (error) {
        stop();
        throw error;
      }
    },
  };
}
