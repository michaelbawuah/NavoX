import { type AccountAccess, restoreAccountAccess } from "./account-access";

export const ACCOUNT_SESSION_CHANGING_EVENT = "navox-account-session-changing";
export const ACCOUNT_SESSION_CHANGED_EVENT = "navox-account-session-changed";
const channelName = "navox-account-session";
const maxSources = 64;
const maxChanges = 64;
const checkingTimeoutMs = 30_000;
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type AccountSessionCallbacks = {
  checking: () => void;
  restored: (access: AccountAccess) => void;
  unavailable: () => void;
  /** Auth mutations terminate the old call, even when the restored owner is unchanged. */
  revoking?: () => void;
};

export type AccountSessionOptions = {
  apiBaseUrl?: string;
  /** A provider may read only auth/me; the legacy workspace also restores connections. */
  restoreAccess?: () => Promise<AccountAccess>;
};

export type AccountSessionChange = Readonly<{ id: string }>;
type Phase = "session-changing" | "session-changed";
type Notice = {
  schema: "navox-account-session.v1";
  sender: string;
  sequence: number;
  phase: Phase;
  activeChanges: string[];
};
type SessionListener = (phase: Phase) => void;

const listeners = new Set<SessionListener>();
const activeLocalChanges = new Set<string>();
const remoteStates = new Map<
  string,
  { sequence: number; activeChanges: string[] }
>();
let senderId: string | undefined;
let sequence = 0;
let channel: BroadcastChannel | null = null;
let transportStarted = false;
let transportBlocked = false;
let legacyChanging = false;
let localEventChanging = false;

function identity() {
  senderId ??=
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `session-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return senderId;
}

function held() {
  return (
    transportBlocked ||
    legacyChanging ||
    localEventChanging ||
    activeLocalChanges.size > 0 ||
    [...remoteStates.values()].some((state) => state.activeChanges.length > 0)
  );
}

function emit(phase: Phase) {
  for (const listener of [...listeners]) {
    try {
      listener(phase);
    } catch {
      // One consumer cannot prevent the remaining consumers from revoking access.
    }
  }
}

function notice(value: unknown): Notice | null {
  if (!value || typeof value !== "object") return null;
  const input = value as Partial<Notice>;
  if (
    input.schema !== "navox-account-session.v1" ||
    typeof input.sender !== "string" ||
    !input.sender ||
    input.sender.length > 128 ||
    !Number.isSafeInteger(input.sequence) ||
    (input.sequence ?? 0) <= 0 ||
    (input.phase !== "session-changing" && input.phase !== "session-changed") ||
    !Array.isArray(input.activeChanges) ||
    input.activeChanges.length > maxChanges ||
    (input.phase === "session-changing" && input.activeChanges.length === 0) ||
    input.activeChanges.some(
      (id) => typeof id !== "string" || !id || id.length > 128,
    ) ||
    new Set(input.activeChanges).size !== input.activeChanges.length
  ) {
    return null;
  }
  return input as Notice;
}

function receiveRemote(event: MessageEvent<unknown>) {
  const update = notice(event.data);
  if (!update) {
    // Retain the old channel's string-message refresh contract. A legacy hold
    // is released only by its corresponding legacy changed notification.
    if (event.data === "session-changing") {
      legacyChanging = true;
      emit("session-changing");
    } else if (event.data === "session-changed" || event.data === "changed") {
      legacyChanging = false;
      emit("session-changed");
    }
    return;
  }
  if (update.sender === identity()) return;
  const previous = remoteStates.get(update.sender);
  if (previous && update.sequence <= previous.sequence) return;
  if (!previous && remoteStates.size >= maxSources) {
    // Unexpectedly many origin contexts fail closed with constant storage.
    transportBlocked = true;
    emit("session-changing");
    return;
  }
  remoteStates.set(update.sender, {
    sequence: update.sequence,
    activeChanges: [...update.activeChanges],
  });
  emit(update.phase);
}

function receiveLocal(event: Event) {
  localEventChanging = event.type === ACCOUNT_SESSION_CHANGING_EVENT;
  emit(localEventChanging ? "session-changing" : "session-changed");
}

function startTransport() {
  if (transportStarted) return;
  transportStarted = true;
  window.addEventListener(ACCOUNT_SESSION_CHANGING_EVENT, receiveLocal);
  window.addEventListener(ACCOUNT_SESSION_CHANGED_EVENT, receiveLocal);
  try {
    channel =
      typeof BroadcastChannel === "undefined"
        ? null
        : new BroadcastChannel(channelName);
    channel?.addEventListener("message", receiveRemote);
  } catch {
    channel = null;
  }
}

function stopTransport() {
  if (!transportStarted || listeners.size) return;
  transportStarted = false;
  window.removeEventListener(ACCOUNT_SESSION_CHANGING_EVENT, receiveLocal);
  window.removeEventListener(ACCOUNT_SESSION_CHANGED_EVENT, receiveLocal);
  channel?.removeEventListener("message", receiveRemote);
  channel?.close();
  channel = null;
  // While nobody listens we cannot track remote mutation completion. A later
  // observer starts with a fresh authoritative restoration, not stale remote state.
  remoteStates.clear();
  transportBlocked = false;
  legacyChanging = false;
  localEventChanging = false;
}

function publish(phase: Phase) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new Event(
      phase === "session-changing"
        ? ACCOUNT_SESSION_CHANGING_EVENT
        : ACCOUNT_SESSION_CHANGED_EVENT,
    ),
  );
  const update: Notice = {
    schema: "navox-account-session.v1",
    sender: identity(),
    sequence: ++sequence,
    phase,
    // Full state prevents reordered notices from losing a concurrent mutation hold.
    activeChanges: [...activeLocalChanges],
  };
  let outgoing: BroadcastChannel | null = null;
  try {
    outgoing =
      typeof BroadcastChannel === "undefined"
        ? null
        : new BroadcastChannel(channelName);
    outgoing?.postMessage(update);
  } catch {
    // Local revocation is already synchronous even when cross-tab transport fails.
  } finally {
    outgoing?.close();
  }
}

/** Suspend synchronously before auth/reset I/O; complete this exact token in finally. */
export function notifyAccountSessionChanging(): AccountSessionChange {
  if (activeLocalChanges.size >= maxChanges)
    throw new Error(
      "Too many sign-in changes are pending. Wait before trying again.",
    );
  const token = { id: `${identity()}:${++sequence}` };
  activeLocalChanges.add(token.id);
  publish("session-changing");
  return token;
}

/**
 * Notify every observer, including this document. The zero-argument legacy call
 * refreshes access but cannot release an outstanding mutation's separate token.
 */
export function notifyAccountSessionChanged(change?: AccountSessionChange) {
  if (change) activeLocalChanges.delete(change.id);
  publish("session-changed");
}

/**
 * Remote holds do not silently expire: an interrupted mutating tab must complete
 * its notice, or the receiving app must fully reload and verify access afresh.
 */
export function observeAccountSession(
  callbacks: AccountSessionCallbacks,
  options: AccountSessionOptions = {},
) {
  let version = 0;
  let disposed = false;
  let watchdog: ReturnType<typeof setTimeout> | undefined;
  let watchdogGeneration = 0;
  const restore =
    options.restoreAccess ??
    (() => restoreAccountAccess(options.apiBaseUrl ?? apiBaseUrl));

  function clearWatchdog() {
    ++watchdogGeneration;
    if (watchdog !== undefined) clearTimeout(watchdog);
    watchdog = undefined;
  }

  function watchChecking() {
    if (watchdog !== undefined) return;
    const generation = ++watchdogGeneration;
    watchdog = setTimeout(() => {
      if (disposed || generation !== watchdogGeneration) return;
      clearWatchdog();
      // Fence every outstanding response before reporting failure. The mutation
      // holds remain intact: timeout neither releases them nor starts new I/O.
      ++version;
      try {
        callbacks.revoking?.();
      } finally {
        callbacks.unavailable();
      }
    }, checkingTimeoutMs);
  }

  function suspend() {
    if (disposed) return;
    ++version;
    // Hiding fences pending proof and gates capture, but is not an auth failure.
    // A mutation hold still needs a bounded failure if its completion is lost.
    if (held()) watchChecking();
    else clearWatchdog();
    callbacks.checking();
  }

  async function check() {
    if (disposed) return;
    const request = ++version;
    callbacks.checking();
    if (disposed || request !== version) return;
    if (held()) {
      watchChecking();
      return;
    }
    if (document.visibilityState !== "visible") {
      clearWatchdog();
      return;
    }
    // Only an actual restoration or mutation hold may time out. Ordinary
    // visibility suspension can last indefinitely and rechecks on return.
    watchChecking();
    try {
      const access = await restore();
      if (
        !disposed &&
        request === version &&
        !held() &&
        document.visibilityState === "visible"
      ) {
        clearWatchdog();
        callbacks.restored(access);
      }
    } catch {
      if (!disposed && request === version && !held()) {
        clearWatchdog();
        callbacks.unavailable();
      }
    }
  }

  function visibilityChanged() {
    if (document.visibilityState === "visible") void check();
    else suspend();
  }

  const sessionChanged: SessionListener = (phase) => {
    if (disposed) return;
    // Epoch invalidation precedes even the optional terminal-revocation callback.
    ++version;
    try {
      callbacks.revoking?.();
    } finally {
      if (phase === "session-changing") suspend();
      else void check();
    }
  };

  if (typeof document === "undefined" || typeof window === "undefined") {
    callbacks.checking();
    callbacks.unavailable();
  } else {
    listeners.add(sessionChanged);
    startTransport();
    document.addEventListener("visibilitychange", visibilityChanged);
    void check();
  }

  return {
    /** Recheck after online/pageshow; pending mutation tokens still prevent restoration. */
    refresh: () => {
      void check();
    },
    invalidate: () => {
      ++version;
      clearWatchdog();
    },
    dispose: () => {
      if (disposed) return;
      disposed = true;
      ++version;
      clearWatchdog();
      listeners.delete(sessionChanged);
      if (typeof document !== "undefined")
        document.removeEventListener("visibilitychange", visibilityChanged);
      if (typeof window !== "undefined") stopTransport();
    },
  };
}
