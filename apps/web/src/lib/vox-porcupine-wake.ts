import type {
  WakeWordAdapter,
  WakeWordEvent,
} from "@navox/assistant-runtime/voice";

export interface PorcupineWakeConfig {
  accessKey: string;
  keywordPublicPath: string;
  modelPublicPath: string;
  label?: string;
}

export interface PorcupineWakeWorker {
  readonly worker?: Pick<Worker, "onmessage" | "postMessage">;
  release(): void | Promise<void>;
  terminate(): void | Promise<void>;
}

export interface PorcupineWakeSDK {
  PorcupineWorker: {
    create(
      accessKey: string,
      keywords: { publicPath: string; label: string }[],
      detection: (value: { label: string }) => void,
      model: { publicPath: string },
      options: { processErrorCallback: (error: unknown) => void },
    ): Promise<PorcupineWakeWorker>;
  };
  WebVoiceProcessor: {
    subscribe(worker: PorcupineWakeWorker): Promise<void>;
    unsubscribe(worker: PorcupineWakeWorker): Promise<void>;
  };
}

export interface PorcupineWakeOptions {
  /** Caller must authenticate and authorize this lazy configuration request. */
  loadConfig(): Promise<PorcupineWakeConfig>;
  /** Inject the installed official SDKs; construction never imports or loads them. */
  loadSdk(): Promise<PorcupineWakeSDK>;
  supported?: boolean;
  now?: () => Date;
  onError?: (message: string) => void;
  cleanupTimeoutMs?: number;
}

interface OwnedWorker {
  worker: PorcupineWakeWorker;
  sdk: PorcupineWakeSDK;
  cleanup: Promise<void> | null;
  subscriptionStarted: boolean;
}

const START_ERROR = "On-device wake listening could not start.";
const CLEANUP_ERROR = "On-device wake cleanup could not be confirmed.";

function cancelled(): Error {
  const error = new Error("On-device wake listening was cancelled.");
  error.name = "AbortError";
  return error;
}

function waitFor<T>(
  pending: Promise<T>,
  signal: AbortSignal,
  late?: (value: T) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(cancelled());
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
    void pending.then(
      (value) => {
        signal.removeEventListener("abort", abort);
        if (signal.aborted) late?.(value);
        else resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", abort);
        if (!signal.aborted) reject(error);
      },
    );
  });
}

/**
 * Optional on-device wake seam. No remote speech fallback, config request,
 * microphone, model download or worker is created before explicit start().
 */
export class PorcupineWakeWordAdapter implements WakeWordAdapter {
  readonly id = "porcupine-local-wake";
  readonly supported: boolean;
  private generation = 0;
  private operation: AbortController | null = null;
  private owned: OwnedWorker | null = null;
  private listener: ((event: WakeWordEvent) => void) | null = null;
  private starting = false;
  private listening = false;
  private poisoned = false;
  private cleanup: Promise<void> = Promise.resolve();
  private readonly cleanupTimeoutMs: number;

  constructor(private readonly options: PorcupineWakeOptions) {
    this.supported =
      options.supported ??
      (typeof window !== "undefined" &&
        typeof Worker === "function" &&
        typeof navigator.mediaDevices?.getUserMedia === "function");
    this.cleanupTimeoutMs = Math.max(
      1,
      Math.min(5_000, options.cleanupTimeoutMs ?? 1_000),
    );
  }

  get active(): boolean {
    return (
      this.listening &&
      this.operation !== null &&
      !this.operation.signal.aborted
    );
  }

  private report(message: string): void {
    try {
      this.options.onError?.(message);
    } catch {
      /* Cleanup still proceeds. */
    }
  }

  private async bounded(action: () => void | Promise<void>): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        Promise.resolve().then(action),
        new Promise<never>((_resolve, reject) => {
          timer = setTimeout(
            () => reject(new Error(CLEANUP_ERROR)),
            this.cleanupTimeoutMs,
          );
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }

  private retire(owned: OwnedWorker): Promise<void> {
    if (owned.cleanup) return owned.cleanup;
    owned.cleanup = (async () => {
      let failed = false;
      for (const release of [
        () =>
          owned.subscriptionStarted
            ? owned.sdk.WebVoiceProcessor.unsubscribe(owned.worker)
            : undefined,
        () => owned.worker.release(),
        () => owned.worker.terminate(),
      ]) {
        try {
          await this.bounded(release);
        } catch {
          failed = true;
        }
      }
      if (failed) {
        this.poisoned = true;
        throw new Error(CLEANUP_ERROR);
      }
    })();
    // A handled mirror lets later starts wait without creating unhandled errors.
    this.cleanup = owned.cleanup.catch(() => {});
    return owned.cleanup;
  }

  private retireLate(owned: OwnedWorker): void {
    void this.retire(owned).catch(() => this.report(CLEANUP_ERROR));
  }

  async start(listener: (event: WakeWordEvent) => void): Promise<void> {
    if (!this.supported)
      throw new Error("On-device wake detection is unavailable.");
    if (this.poisoned) throw new Error(CLEANUP_ERROR);
    if (this.starting || this.active)
      throw new Error("Wake listening is already active.");
    this.starting = true;
    const generation = ++this.generation;
    const operation = new AbortController();
    this.operation = operation;
    this.listener = listener;
    const current = () =>
      generation === this.generation && !operation.signal.aborted;
    try {
      await waitFor(this.cleanup, operation.signal);
      if (this.poisoned) throw new Error(CLEANUP_ERROR);
      if (!current()) throw cancelled();
      const config = await waitFor(this.options.loadConfig(), operation.signal);
      if (!current()) throw cancelled();
      const label = config.label?.trim() || "Hey NavoX";
      if (
        !config.accessKey?.trim() ||
        !config.keywordPublicPath?.trim() ||
        !config.modelPublicPath?.trim()
      )
        throw new Error(START_ERROR);
      const sdk = await waitFor(this.options.loadSdk(), operation.signal);
      if (!current()) throw cancelled();
      const detect = (detection: { label: string }) => {
        if (!current() || !this.active || detection?.label !== label) return;
        const notify = this.listener;
        void this.stop().catch(() => this.report(CLEANUP_ERROR));
        notify?.({
          type: "wake",
          detected_at: (this.options.now?.() ?? new Date()).toISOString(),
          device_id: "browser-local-porcupine",
          confidence: 1,
        });
      };
      const worker = await waitFor(
        sdk.PorcupineWorker.create(
          config.accessKey,
          [{ publicPath: config.keywordPublicPath, label }],
          detect,
          { publicPath: config.modelPublicPath },
          {
            processErrorCallback: () => {
              if (!current()) return;
              this.report(START_ERROR);
              void this.stop().catch(() => this.report(CLEANUP_ERROR));
            },
          },
        ),
        operation.signal,
        (late) =>
          this.retireLate({
            worker: late,
            sdk,
            cleanup: null,
            subscriptionStarted: false,
          }),
      );
      const owned: OwnedWorker = {
        worker,
        sdk,
        cleanup: null,
        subscriptionStarted: false,
      };
      if (!current()) {
        this.retireLate(owned);
        throw cancelled();
      }
      this.owned = owned;
      owned.subscriptionStarted = true;
      const subscription = sdk.WebVoiceProcessor.subscribe(worker);
      // A microphone permission prompt may resolve after stop's first unsubscribe.
      const unsubscribeLate = () => {
        if (current()) return;
        void this.bounded(() =>
          sdk.WebVoiceProcessor.unsubscribe(worker),
        ).catch(() => {
          this.poisoned = true;
          this.report(CLEANUP_ERROR);
        });
      };
      void subscription.then(unsubscribeLate, unsubscribeLate);
      await waitFor(subscription, operation.signal);
      if (current()) this.listening = true;
    } catch (error) {
      const wasCancelled = !current();
      if (!wasCancelled) {
        try {
          await this.stop();
        } catch {
          throw new Error(CLEANUP_ERROR);
        }
      }
      if (wasCancelled) throw cancelled();
      if (error instanceof Error && error.message === CLEANUP_ERROR)
        throw error;
      throw new Error(START_ERROR);
    } finally {
      if (generation === this.generation) this.starting = false;
    }
  }

  async stop(): Promise<void> {
    ++this.generation;
    this.listening = this.starting = false;
    this.listener = null;
    this.operation?.abort();
    this.operation = null;
    const owned = this.owned;
    this.owned = null;
    if (owned) await this.retire(owned);
    else await this.cleanup;
    if (this.poisoned) throw new Error(CLEANUP_ERROR);
  }
}

export function createPorcupineWakeWordAdapter(
  options: PorcupineWakeOptions,
): PorcupineWakeWordAdapter {
  return new PorcupineWakeWordAdapter(options);
}
