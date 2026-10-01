import { LIMITS } from "./limits";

export interface PurgeScheduler {
  start(): void;
  stop(): void;
  /** One bounded purge pass. Never overlaps a pass that is still running. */
  runOnce(): Promise<number>;
}

export interface PurgeSchedulerDeps {
  purge: (now: string) => Promise<number>;
  intervalMs?: number;
  now?: () => Date;
  onError?: (error: unknown) => void;
  setIntervalImpl?: (handler: () => void, ms: number) => unknown;
  clearIntervalImpl?: (handle: unknown) => void;
}

/**
 * Bounded retention purge on a timer.
 *
 * Each pass deletes at most one bounded batch, passes never overlap, and the
 * timer is unref'd so it cannot hold a server process open. This makes the
 * 30-day expiry an access bound with a scheduled cleanup rather than a promise
 * of exact deletion time.
 */
export function createPurgeScheduler(deps: PurgeSchedulerDeps): PurgeScheduler {
  const intervalMs = deps.intervalMs ?? LIMITS.purgeIntervalMs;
  const now = deps.now ?? (() => new Date());
  const setIntervalImpl =
    deps.setIntervalImpl ??
    ((handler: () => void, ms: number) => setInterval(handler, ms));
  const clearIntervalImpl =
    deps.clearIntervalImpl ??
    ((handle: unknown) =>
      clearInterval(handle as ReturnType<typeof setInterval>));
  let handle: unknown = null;
  let running = false;

  const runOnce = async (): Promise<number> => {
    if (running) return 0;
    running = true;
    try {
      return await deps.purge(now().toISOString());
    } catch (error) {
      deps.onError?.(error);
      return 0;
    } finally {
      running = false;
    }
  };

  return {
    runOnce,

    start() {
      if (handle !== null) return;
      handle = setIntervalImpl(() => {
        void runOnce();
      }, intervalMs);
      const unref = (handle as { unref?: () => void }).unref;
      if (typeof unref === "function") unref.call(handle);
    },

    stop() {
      if (handle === null) return;
      clearIntervalImpl(handle);
      handle = null;
    },
  };
}
