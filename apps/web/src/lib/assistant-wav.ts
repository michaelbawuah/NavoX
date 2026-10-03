/**
 * The SPEC-005 recorded-clip contract in one dependency-free module.
 *
 * The browser encoder and the Next route validator both use this file so a
 * clip is measured by the same rules on both sides of the wire: uncompressed
 * PCM WAV, mono, 16 kHz, 16-bit, 200 ms to 30 s, at most 1,000,000 bytes.
 * Nothing here reads or stores audio; it only shapes and inspects bytes.
 */

export const WAV_SAMPLE_RATE = 16_000;
export const WAV_CHANNELS = 1;
export const WAV_SAMPLE_WIDTH_BYTES = 2;
export const WAV_BITS_PER_SAMPLE = 16;
export const MAX_WAV_BYTES = 1_000_000;
export const MIN_WAV_MILLISECONDS = 200;
export const MAX_WAV_MILLISECONDS = 30_000;

const HEADER_BYTES = 44;

/** A clip the SPEC-005 contract cannot accept. Messages are safe to show. */
export class SpeechClipError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SpeechClipError";
  }
}

export interface WavInfo {
  channels: number;
  sampleRate: number;
  bitsPerSample: number;
  /** Bytes of PCM frame data, excluding the container header. */
  dataBytes: number;
  /** Duration derived from the frame count, never a client claim. */
  durationMilliseconds: number;
}

function ascii(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.subarray(offset, offset + length));
}

/**
 * Linear-interpolation resample to the 16 kHz transcription rate. A device
 * already at 16 kHz is copied so the caller never shares a live buffer.
 */
export function resampleTo16k(
  samples: Float32Array,
  sourceRate: number,
): Float32Array {
  if (!Number.isFinite(sourceRate) || sourceRate <= 0) {
    throw new SpeechClipError(
      "The microphone reported an unusable sample rate. Type your question instead.",
    );
  }
  if (sourceRate === WAV_SAMPLE_RATE) return samples.slice();
  const ratio = sourceRate / WAV_SAMPLE_RATE;
  const outputLength = Math.max(0, Math.floor(samples.length / ratio));
  const output = new Float32Array(outputLength);
  for (let index = 0; index < outputLength; index += 1) {
    const position = index * ratio;
    const lower = Math.floor(position);
    const upper = Math.min(lower + 1, samples.length - 1);
    const weight = position - lower;
    const start = samples[lower] ?? 0;
    const end = samples[upper] ?? start;
    output[index] = start + (end - start) * weight;
  }
  return output;
}

function toPcm16(sample: number): number {
  const clamped = sample < -1 ? -1 : sample > 1 ? 1 : sample;
  return clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
}

/**
 * Encodes mono float samples as an uncompressed 16 kHz 16-bit PCM WAV. The
 * view is `ArrayBuffer`-backed so it is directly usable as a fetch body.
 */
export function encodeWavPcm16(
  samples: Float32Array,
  sourceRate: number,
): Uint8Array<ArrayBuffer> {
  const mono = resampleTo16k(samples, sourceRate);
  const dataBytes = mono.length * WAV_SAMPLE_WIDTH_BYTES;
  const buffer = new ArrayBuffer(HEADER_BYTES + dataBytes);
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  const blockAlign = WAV_CHANNELS * WAV_SAMPLE_WIDTH_BYTES;
  const byteRate = WAV_SAMPLE_RATE * blockAlign;

  for (const [offset, text] of [
    [0, "RIFF"],
    [8, "WAVE"],
    [12, "fmt "],
    [36, "data"],
  ] as const) {
    for (let index = 0; index < text.length; index += 1) {
      bytes[offset + index] = text.charCodeAt(index);
    }
  }
  view.setUint32(4, HEADER_BYTES - 8 + dataBytes, true);
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, WAV_CHANNELS, true);
  view.setUint32(24, WAV_SAMPLE_RATE, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, WAV_BITS_PER_SAMPLE, true);
  view.setUint32(40, dataBytes, true);
  for (let index = 0; index < mono.length; index += 1) {
    view.setInt16(
      HEADER_BYTES + index * WAV_SAMPLE_WIDTH_BYTES,
      toPcm16(mono[index] ?? 0),
      true,
    );
  }
  return bytes;
}

/**
 * Parses and bounds one WAV container. The frames are read only to prove the
 * clip is complete and to derive its duration; a bare PCM buffer, a WebM blob
 * or a mono/rate mismatch is refused rather than relabelled.
 */
export function parseAssistantWav(audio: Uint8Array<ArrayBuffer>): WavInfo {
  if (audio.byteLength === 0) {
    throw new SpeechClipError("Record a question before sending it.");
  }
  if (audio.byteLength > MAX_WAV_BYTES) {
    throw new SpeechClipError(
      "That recording is too large. Record a shorter question.",
    );
  }
  if (
    audio.byteLength < HEADER_BYTES + WAV_SAMPLE_WIDTH_BYTES ||
    ascii(audio, 0, 4) !== "RIFF" ||
    ascii(audio, 8, 4) !== "WAVE"
  ) {
    throw new SpeechClipError(
      "The recording must be an uncompressed WAV clip.",
    );
  }

  const view = new DataView(audio.buffer, audio.byteOffset, audio.byteLength);
  let offset = 12;
  let format: {
    audioFormat: number;
    channels: number;
    sampleRate: number;
    bitsPerSample: number;
    blockAlign: number;
  } | null = null;
  let dataBytes: number | null = null;
  while (offset + 8 <= audio.byteLength) {
    const id = ascii(audio, offset, 4);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === "fmt ") {
      if (size < 16 || body + 16 > audio.byteLength) {
        throw new SpeechClipError("The WAV header is incomplete.");
      }
      format = {
        audioFormat: view.getUint16(body, true),
        channels: view.getUint16(body + 2, true),
        sampleRate: view.getUint32(body + 4, true),
        blockAlign: view.getUint16(body + 12, true),
        bitsPerSample: view.getUint16(body + 14, true),
      };
    } else if (id === "data") {
      if (body + size > audio.byteLength) {
        throw new SpeechClipError("The WAV file has incomplete frame data.");
      }
      dataBytes = size;
    }
    offset = body + size + (size % 2);
  }

  if (!format) throw new SpeechClipError("The recording has no audio format.");
  if (dataBytes === null) {
    throw new SpeechClipError("The recording has no audio frames.");
  }
  const expectedBlockAlign = format.channels * WAV_SAMPLE_WIDTH_BYTES;
  if (
    format.audioFormat !== 1 ||
    format.channels !== WAV_CHANNELS ||
    format.sampleRate !== WAV_SAMPLE_RATE ||
    format.bitsPerSample !== WAV_BITS_PER_SAMPLE ||
    format.blockAlign !== expectedBlockAlign
  ) {
    throw new SpeechClipError(
      "Audio must be a 16 kHz mono 16-bit uncompressed WAV clip.",
    );
  }
  if (dataBytes === 0 || dataBytes % expectedBlockAlign !== 0) {
    throw new SpeechClipError("The WAV file has incomplete frame data.");
  }
  const frames = dataBytes / expectedBlockAlign;
  const durationMilliseconds = Math.floor((frames * 1000) / WAV_SAMPLE_RATE);
  if (
    durationMilliseconds < MIN_WAV_MILLISECONDS ||
    durationMilliseconds > MAX_WAV_MILLISECONDS
  ) {
    throw new SpeechClipError(
      "Record between 0.2 and 30 seconds of speech, then try again.",
    );
  }
  return {
    channels: format.channels,
    sampleRate: format.sampleRate,
    bitsPerSample: format.bitsPerSample,
    dataBytes,
    durationMilliseconds,
  };
}
