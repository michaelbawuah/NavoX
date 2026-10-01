/**
 * Local, bounded speech activity detection for opt-in Hands-Free capture.
 * Audio frames stay in the browser; this detector emits only start/end signals.
 * It is deliberately conservative while NavoX speaks so ordinary speaker echo
 * does not become a new AssistantTurn.
 */
export interface VoiceActivity {
  started: boolean;
  ended: boolean;
  heardSpeech: boolean;
}

export class VoiceActivityDetector {
  private ambient = 0.008;
  private voicedMs = 0;
  private quietMs = 0;
  private active = false;
  private finished = false;

  constructor(private readonly whileSpeakerPlays: boolean) {}

  observe(frame: Float32Array, sampleRate: number): VoiceActivity {
    if (this.finished || frame.length === 0 || sampleRate <= 0) {
      return { started: false, ended: false, heardSpeech: this.active };
    }
    const durationMs = (frame.length / sampleRate) * 1_000;
    let energy = 0;
    for (const sample of frame) energy += sample * sample;
    const rms = Math.sqrt(energy / frame.length);
    // Browser echo cancellation is requested by the capture adapter. The
    // higher speaker threshold makes the local detector fail quiet if echo
    // cancellation is ineffective; real-device acceptance remains required.
    const minimum = this.whileSpeakerPlays ? 0.055 : 0.025;
    const ratio = this.whileSpeakerPlays ? 3.5 : 2.5;
    const voiced = rms >= Math.max(minimum, this.ambient * ratio);
    if (!this.active && !voiced) {
      this.ambient = Math.min(0.08, this.ambient * 0.92 + rms * 0.08);
    }
    if (!this.active) {
      this.voicedMs = voiced ? this.voicedMs + durationMs : 0;
      const requiredMs = this.whileSpeakerPlays ? 180 : 120;
      if (this.voicedMs >= requiredMs) {
        this.active = true;
        this.quietMs = 0;
        return { started: true, ended: false, heardSpeech: true };
      }
      return { started: false, ended: false, heardSpeech: false };
    }
    this.quietMs = voiced ? 0 : this.quietMs + durationMs;
    if (this.quietMs >= 850) {
      this.finished = true;
      return { started: false, ended: true, heardSpeech: true };
    }
    return { started: false, ended: false, heardSpeech: true };
  }
}
