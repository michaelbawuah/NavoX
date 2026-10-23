import type { VoxActivity } from "./vox-call";

export interface VoxActivityOptions {
  /** Only this admitted agent may contribute remote speaking activity. */
  agentIdentity: string;
  mayTransmitAudio(): boolean;
  mayPlayAudio(): boolean;
  onChange(activity: VoxActivity): void;
}

/** Content-free call activity. Track presence alone is never speech activity. */
export class VoxActivityTracker {
  private localSpeech = false;
  private agentSpeech = false;
  private audiblePlayback = false;
  private published: VoxActivity = "quiet";

  constructor(private readonly options: VoxActivityOptions) {}

  local(speech: boolean): void {
    this.localSpeech = speech === true;
    this.refresh();
  }

  remote(identity: string, speaking: boolean): void {
    if (identity !== this.options.agentIdentity) return;
    this.agentSpeech = speaking === true;
    this.refresh();
  }

  playback(enabled: boolean): void {
    this.audiblePlayback = enabled === true;
    this.refresh();
  }

  current(): VoxActivity {
    if (this.localSpeech && this.permitted(this.options.mayTransmitAudio))
      return "listening";
    if (
      this.agentSpeech &&
      this.audiblePlayback &&
      this.permitted(this.options.mayPlayAudio)
    )
      return "speaking";
    return "quiet";
  }

  /** Recheck live authority after mute, owner verification, or visibility changes. */
  refresh(): void {
    const next = this.current();
    if (next === this.published) return;
    this.published = next;
    this.options.onChange(next);
  }

  reset(): void {
    this.localSpeech = this.agentSpeech = this.audiblePlayback = false;
    this.refresh();
  }

  private permitted(check: () => boolean): boolean {
    try {
      return check() === true;
    } catch {
      return false;
    }
  }
}
