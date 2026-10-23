import type {
  WakeWordAdapter,
  WakeWordEvent,
} from "@navox/assistant-runtime/voice";
import { createBrowserWakeWordAdapter } from "./assistant-wake";
import {
  createPorcupineWakeWordAdapter,
  type PorcupineWakeConfig,
  type PorcupineWakeSDK,
} from "./vox-porcupine-wake";

export interface VoxWakeBinding {
  /** Private, current-connection read. Null means licensed wake is not configured. */
  loadConfig(signal: AbortSignal): Promise<PorcupineWakeConfig | null>;
}

function cancelled() {
  const error = new Error("Local wake listening was cancelled.");
  error.name = "AbortError";
  return error;
}

function abortable<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(cancelled());
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
    void pending.then(
      (value) => {
        signal.removeEventListener("abort", abort);
        if (!signal.aborted) resolve(value);
      },
      () => {
        signal.removeEventListener("abort", abort);
        if (!signal.aborted)
          reject(new Error("Local wake listening could not start."));
      },
    );
  });
}

/**
 * One admitted connection owns configuration and detector lifetime. Construction
 * is inert. Browser-visible credentials are never cached or written to storage.
 */
export function createRuntimeVoxWakeWordAdapter(
  binding: VoxWakeBinding,
  options: {
    loadSdk?: () => Promise<PorcupineWakeSDK>;
    nativeFactory?: () => WakeWordAdapter;
    porcupineSupported?: boolean;
  } = {},
): WakeWordAdapter {
  const native = (options.nativeFactory ?? createBrowserWakeWordAdapter)();
  const porcupineSupported =
    options.porcupineSupported ??
    (typeof window !== "undefined" &&
      window.isSecureContext === true &&
      typeof Worker === "function" &&
      typeof WebAssembly === "object" &&
      typeof indexedDB !== "undefined" &&
      typeof navigator.mediaDevices?.getUserMedia === "function");
  let selected: WakeWordAdapter | null = null;
  let operation: AbortController | null = null;
  let generation = 0;
  let starting = false;
  let unsafe = false;
  let cleanup: Promise<void> = Promise.resolve();
  const retire = (adapter: WakeWordAdapter | null): Promise<void> => {
    let stopped: Promise<void>;
    try {
      stopped = Promise.resolve(adapter?.stop());
    } catch {
      stopped = Promise.reject(
        new Error("Local wake cleanup could not be confirmed."),
      );
    }
    cleanup = Promise.all([cleanup.catch(() => {}), stopped])
      .then(() => {
        if (unsafe)
          throw new Error("Local wake cleanup could not be confirmed.");
      })
      .catch(() => {
        unsafe = true;
        throw new Error("Local wake cleanup could not be confirmed.");
      });
    void cleanup.catch(() => {});
    return cleanup;
  };

  return {
    id: "configured-local-wake",
    supported: porcupineSupported || native.supported,
    get active() {
      return (
        operation !== null &&
        !operation.signal.aborted &&
        selected?.active === true
      );
    },
    async start(listener: (event: WakeWordEvent) => void) {
      if (starting || selected?.active || unsafe)
        throw new Error("Local wake listening is unavailable.");
      starting = true;
      const currentGeneration = ++generation;
      const controller = new AbortController();
      operation = controller;
      const current = () =>
        currentGeneration === generation && !controller.signal.aborted;
      try {
        await abortable(cleanup, controller.signal);
        if (!current()) throw cancelled();
        if (unsafe) throw new Error("Local wake listening is unavailable.");
        const config = porcupineSupported
          ? await abortable(
              binding.loadConfig(controller.signal),
              controller.signal,
            )
          : null;
        if (!current()) throw cancelled();
        const adapter = config
          ? createPorcupineWakeWordAdapter({
              supported: porcupineSupported,
              loadConfig: async () => config,
              loadSdk: options.loadSdk,
            })
          : native;
        if (!adapter.supported)
          throw new Error("Local wake listening is unavailable.");
        selected = adapter;
        await abortable(
          adapter.start((event) => {
            if (current() && selected === adapter) listener(event);
          }),
          controller.signal,
        );
        if (!current()) throw cancelled();
        if (!adapter.active)
          throw new Error("Local wake listening could not start.");
      } catch {
        if (current()) {
          operation = null;
          controller.abort();
          const retired = selected;
          selected = null;
          try {
            await retire(retired);
          } catch {
            unsafe = true;
          }
          throw new Error("Local wake listening could not start.");
        }
        throw cancelled();
      } finally {
        if (currentGeneration === generation) starting = false;
      }
    },
    async stop() {
      ++generation;
      starting = false;
      operation?.abort();
      operation = null;
      const retired = selected;
      selected = null;
      try {
        await retire(retired);
      } catch {
        unsafe = true;
        throw new Error("Local wake cleanup could not be confirmed.");
      }
      if (unsafe) throw new Error("Local wake cleanup could not be confirmed.");
    },
  };
}
