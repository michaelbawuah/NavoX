import type {
  WakeWordAdapter,
  WakeWordEvent,
} from "@navox/assistant-runtime/voice";

/** Factories construct inert, on-device adapters; start alone may open local audio. */
export type VoxWakeFactory = () => WakeWordAdapter;
export type VoxWakeStatus = "off" | "starting" | "listening" | "unavailable";

/** One admitted connection owns this listener; no audio or text is retained here. */
export class VoxStandbyWake {
  private enabled = false;
  private epoch = 0;
  private status: VoxWakeStatus = "off";
  private transitions = Promise.resolve();
  private cleanupFailed = false;
  private failed = false;

  constructor(
    private readonly options: {
      adapter: WakeWordAdapter | null;
      mayListen: () => boolean;
      onWake: (event: WakeWordEvent) => void;
      onStatus: (status: VoxWakeStatus) => void;
      onUnsafe: () => void;
    },
  ) {}

  get supported(): boolean {
    return this.options.adapter?.supported === true && !this.failed;
  }

  private publish(status: VoxWakeStatus) {
    if (this.status === status) return;
    this.status = status;
    this.options.onStatus(status);
  }

  setEnabled(value: boolean): void {
    const enabled = value && this.supported && this.options.mayListen();
    if (enabled === this.enabled) return;
    this.enabled = enabled;
    const epoch = ++this.epoch;
    const adapter = this.options.adapter;
    if (!enabled) {
      this.publish(this.supported ? "off" : "unavailable");
      if (!adapter) return;
      // stop revokes its callback/microphone synchronously, even during start.
      let stopping: Promise<void>;
      try {
        stopping = adapter.stop();
      } catch {
        stopping = Promise.reject(new Error("Local wake cleanup failed."));
      }
      const stopped = stopping.catch(() => {
        this.cleanupFailed = true;
        this.options.onUnsafe();
      });
      this.transitions = Promise.all([this.transitions, stopped]).then(
        () => {},
      );
      return;
    }
    this.publish("starting");
    this.transitions = this.transitions
      .then(async () => {
        if (
          epoch !== this.epoch ||
          !this.enabled ||
          !this.options.mayListen() ||
          !adapter
        )
          return;
        await adapter.start((event) => {
          if (
            epoch !== this.epoch ||
            !this.enabled ||
            !this.options.mayListen()
          )
            return;
          this.setEnabled(false);
          const stoppedEpoch = this.epoch;
          // Reopen ordinary capture only after the local detector releases its mic.
          void this.transitions.then(() => {
            if (
              stoppedEpoch === this.epoch &&
              !this.cleanupFailed &&
              this.options.mayListen()
            )
              this.options.onWake(event);
          });
        });
        if (epoch !== this.epoch || !this.enabled) return;
        if (!this.options.mayListen()) {
          this.setEnabled(false);
          return;
        }
        if (!adapter.active) throw new Error("Local wake did not start.");
        this.publish("listening");
      })
      .catch(() => {
        if (epoch !== this.epoch) return;
        this.failed = true;
        this.setEnabled(false);
        this.publish("unavailable");
      });
  }

  refresh(): void {
    if (!this.enabled) return;
    if (!this.options.mayListen()) {
      this.setEnabled(false);
      return;
    }
    if (this.status === "listening" && !this.options.adapter?.active) {
      this.failed = true;
      this.setEnabled(false);
      this.publish("unavailable");
    }
  }

  async close(): Promise<void> {
    this.setEnabled(false);
    await this.transitions;
    if (this.cleanupFailed) throw new Error("Local wake cleanup failed.");
  }
}
