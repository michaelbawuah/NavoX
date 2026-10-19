"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useLayoutEffect,
  useState,
} from "react";
import type { Account, AccountAccess } from "../lib/account-access";
import { observeAccountSession } from "../lib/account-session";
import {
  VoxCallController,
  type VoxCallState,
  type VoxTransport,
} from "../lib/vox-call";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const uuid =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const unavailableTransport: VoxTransport = {
  available: false,
  localWakeAvailable: false,
  async connect() {
    throw new Error("Vox calls are not available on this deployment.");
  },
};
const newCallId = () => crypto.randomUUID();

/** Identity checks never depend on permission to list connected Google accounts. */
export async function restoreVoxAccountAccess(): Promise<AccountAccess> {
  const response = await fetch(`${apiBaseUrl}/auth/me`, {
    credentials: "include",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (response.status === 401 || response.status === 403)
    return { account: null, connections: [], connectionsError: "" };
  if (!response.ok) throw new Error("Your sign-in could not be checked.");
  const value: unknown = await response.json();
  if (!value || typeof value !== "object" || !("workspace" in value))
    throw new Error("Your sign-in could not be checked.");
  const workspace = value.workspace;
  if (
    !("id" in value) ||
    typeof value.id !== "string" ||
    !uuid.test(value.id) ||
    !workspace ||
    typeof workspace !== "object" ||
    !("id" in workspace) ||
    typeof workspace.id !== "string" ||
    !uuid.test(workspace.id)
  )
    throw new Error("Your sign-in could not be checked.");
  // Only the two validated IDs enter call state. Other account fields are never
  // retained by this provider or sent to the transport.
  return { account: value as Account, connections: [], connectionsError: "" };
}

export interface VoxCallContextValue {
  controller: VoxCallController;
  state: VoxCallState;
  available: boolean;
}

const VoxContext = createContext<VoxCallContextValue | null>(null);

/** Null means this deployment has no app-scoped Vox owner. */
export function useVoxCall(): VoxCallContextValue | null {
  return useContext(VoxContext);
}

type Configuration = {
  transport: VoxTransport;
  restoreAccess: () => Promise<AccountAccess>;
  createCallId: () => string;
  now?: () => number;
};
type Binding = Configuration & { value: VoxCallContextValue };

/**
 * Mount above route children. A page may subscribe or minimize, but never owns
 * cleanup. Configuration is immutable: replacing it terminates the old lease.
 * The default transport is unavailable; mounting never starts a call or mic.
 */
export function VoxCallProvider({
  children,
  transport = unavailableTransport,
  restoreAccess = restoreVoxAccountAccess,
  createCallId = newCallId,
  now,
}: {
  children: ReactNode;
} & Partial<Configuration>) {
  const [binding, setBinding] = useState<Binding | null>(null);

  useLayoutEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setInterval> | null = null;
    const controller = new VoxCallController({
      owner: null,
      transport,
      newCallId: createCallId,
      now,
    });
    const publish = (state: VoxCallState) => {
      if (!live) return;
      setBinding({
        transport,
        restoreAccess,
        createCallId,
        now,
        value: { controller, state, available: transport.available },
      });
      const ongoing = ["active", "standby", "reconnecting"].includes(
        state.phase,
      );
      if (ongoing && timer === null)
        timer = setInterval(() => controller.tick(), 1_000);
      else if (!ongoing && timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const unsubscribe = controller.subscribe(publish);
    publish(controller.current());
    const forgetOwner = () => {
      void controller.bindOwner(null);
    };
    const observer = observeAccountSession(
      {
        checking: () => controller.suspendForSessionCheck(),
        revoking: forgetOwner,
        restored: (access) => {
          if (
            document.visibilityState !== "visible" ||
            navigator.onLine === false
          ) {
            controller.suspendForSessionCheck();
            return;
          }
          try {
            const account = access.account;
            void controller
              .bindOwner(
                account
                  ? { userId: account.id, workspaceId: account.workspace.id }
                  : null,
              )
              .catch(forgetOwner);
          } catch {
            forgetOwner();
          }
        },
        unavailable: forgetOwner,
      },
      { restoreAccess },
    );
    const leaveDocument = () => {
      observer.invalidate();
      forgetOwner();
    };
    const checkAgain = () => observer.refresh();
    const offline = () => {
      observer.invalidate();
      controller.suspendForSessionCheck();
    };
    window.addEventListener("pagehide", leaveDocument);
    window.addEventListener("beforeunload", leaveDocument);
    window.addEventListener("pageshow", checkAgain);
    window.addEventListener("offline", offline);
    window.addEventListener("online", checkAgain);
    return () => {
      live = false;
      observer.dispose();
      unsubscribe();
      if (timer !== null) clearInterval(timer);
      window.removeEventListener("pagehide", leaveDocument);
      window.removeEventListener("beforeunload", leaveDocument);
      window.removeEventListener("pageshow", checkAgain);
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", checkAgain);
      // dispose revokes synchronously; awaiting an adapter's eventual cleanup
      // never keeps a departed document's audio or wake lease authorized.
      void controller.dispose();
    };
  }, [transport, restoreAccess, createCallId, now]);

  const value =
    binding?.transport === transport &&
    binding.restoreAccess === restoreAccess &&
    binding.createCallId === createCallId &&
    binding.now === now
      ? binding.value
      : null;
  return <VoxContext.Provider value={value}>{children}</VoxContext.Provider>;
}
