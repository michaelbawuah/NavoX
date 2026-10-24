/**
 * Browser-local PCM bridge. Only the main-thread permission/VAD gate can return
 * output; raw microphone frames are never connected directly to publication.
 * Keep this versioned asset at the same origin as the application.
 */
class VoxPcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.enabled = false;
    this.stopped = false;
    this.input = new Float32Array(1024);
    this.inputOffset = 0;
    this.sequence = 0;
    this.clock = 0;
    this.pending = [];
    this.output = [];
    this.outputOffset = 0;
    this.buffered = 0;
    this.limit = Math.ceil(sampleRate * 0.35);
    this.port.onmessage = ({ data }) => {
      if (this.stopped) return;
      if (data?.type === "stop") {
        this.clear();
        return;
      }
      if (data?.type === "enabled" && !this.enabled) {
        this.enabled = true;
        return;
      }
      if (
        !this.enabled ||
        data?.type !== "output" ||
        data.id !== this.pending[0]?.id ||
        !(data.frame instanceof Float32Array) ||
        data.frame.length !== 1024 ||
        this.buffered + data.frame.length > this.limit
      ) {
        this.fail("invalid-response");
        return;
      }
      for (const value of data.frame) {
        if (!Number.isFinite(value) || Math.abs(value) > 1) {
          data.frame.fill(0);
          this.fail("invalid-output");
          return;
        }
      }
      this.pending.shift();
      this.output.push(data.frame);
      this.buffered += data.frame.length;
    };
  }

  clear() {
    this.stopped = true;
    this.enabled = false;
    this.input.fill(0);
    for (const frame of this.output) frame.fill(0);
    this.output = [];
    this.pending = [];
    this.buffered = 0;
    this.inputOffset = this.outputOffset = 0;
  }

  fail(reason) {
    this.clear();
    this.port.postMessage({ type: "fault", reason });
  }

  process(inputs, outputs) {
    const output = outputs[0]?.[0];
    for (const bus of outputs) for (const channel of bus) channel.fill(0);
    if (this.stopped) return false;
    if (!this.enabled) return true;
    const input = inputs[0]?.[0];
    if (
      !output ||
      !input ||
      inputs[0].length !== 1 ||
      outputs[0].length !== 1 ||
      input.length !== output.length ||
      input.length === 0 ||
      input.length > 8192
    ) {
      this.fail("input-layout");
      return false;
    }
    this.clock += input.length;
    if (this.pending.length && this.clock - this.pending[0].at > this.limit) {
      this.fail("gate-timeout");
      return false;
    }
    for (const value of input) {
      if (!Number.isFinite(value)) {
        this.fail("invalid-input");
        return false;
      }
      // Web Audio gain/processing can produce finite peaks beyond full scale.
      // Saturate those peaks before the PCM gate; never publish raw input.
      this.input[this.inputOffset++] = Math.max(-1, Math.min(1, value));
      if (this.inputOffset === this.input.length) {
        const frame = this.input;
        const id = this.sequence++;
        this.pending.push({ id, at: this.clock });
        this.port.postMessage({ type: "frame", id, frame }, [frame.buffer]);
        this.input = new Float32Array(1024);
        this.inputOffset = 0;
      }
    }
    let written = 0;
    while (written < output.length && this.output.length) {
      const frame = this.output[0];
      const count = Math.min(
        output.length - written,
        frame.length - this.outputOffset,
      );
      output.set(
        frame.subarray(this.outputOffset, this.outputOffset + count),
        written,
      );
      written += count;
      this.outputOffset += count;
      this.buffered -= count;
      if (this.outputOffset === frame.length) {
        frame.fill(0);
        this.output.shift();
        this.outputOffset = 0;
      }
    }
    return true;
  }
}

registerProcessor("navox-vox-pcm-v1", VoxPcmProcessor);
