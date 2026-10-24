"use client";
import { useEffect, useRef, useState } from "react";
import { useVoxCall } from "./vox-call-provider";

/** An explicit request chip uses the same private queue as the Vox composer. */
export function useVoxRequest() {
  const call = useVoxCall();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const operation = useRef<AbortController | null>(null);
  const retry = useRef<{ callId: string; text: string; id: string } | null>(
    null,
  );
  const owner = call?.state.owner;
  const ownerKey = owner ? `${owner.workspaceId}:${owner.userId}` : "";
  // biome-ignore lint/correctness/useExhaustiveDependencies: Requests and retry IDs must be cleared whenever the signed-in owner changes.
  useEffect(() => {
    setNotice("");
    setBusy(false);
    retry.current = null;
    return () => {
      operation.current?.abort();
      operation.current = null;
    };
  }, [ownerKey]);
  async function ask(text: string) {
    if (!call?.available || !call.state.ownerVerified || operation.current)
      return;
    const abort = new AbortController();
    operation.current = abort;
    setBusy(true);
    setNotice("");
    let unsubscribe = () => {};
    try {
      const controller = call.controller;
      const initial = controller.current();
      if (["idle", "ended", "unavailable"].includes(initial.phase))
        await controller.start();
      if (abort.signal.aborted) return;
      controller.setMinimized(false);
      if (controller.current().phase === "standby") controller.resume();
      const state = controller.current();
      if (
        !state.ownerVerified ||
        !state.owner ||
        !state.callId ||
        `${state.owner.workspaceId}:${state.owner.userId}` !== ownerKey ||
        !["active", "standby"].includes(state.phase)
      )
        throw Error("Wait until Vox connects, then try your request again.");
      const callId = state.callId;
      unsubscribe = controller.subscribe((next) => {
        if (
          !next.ownerVerified ||
          next.callId !== callId ||
          !next.owner ||
          `${next.owner.workspaceId}:${next.owner.userId}` !== ownerKey ||
          !["active", "standby", "reconnecting"].includes(next.phase)
        )
          abort.abort();
      });
      const request =
        retry.current?.callId === callId && retry.current.text === text
          ? retry.current
          : { callId, text, id: crypto.randomUUID() };
      retry.current = request;
      const base =
        process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
      const response = await fetch(`${base}/vox/calls/${callId}/messages`, {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-NavoX-Account": ownerKey,
        },
        body: JSON.stringify({ request_id: request.id, text }),
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(12000)]),
      });
      if (!response.ok)
        throw Error(
          "Couldn’t confirm your request. Try again when Vox reconnects.",
        );
      const value = await response.json();
      if (
        value.request_id !== request.id ||
        value.status !== "accepted" ||
        value.delivery !== "queued"
      )
        throw Error(
          "Couldn’t confirm your request. Try again when Vox reconnects.",
        );
      if (!abort.signal.aborted) {
        retry.current = null;
        setNotice("Your request is with Vox.");
      }
    } catch (error) {
      if (!abort.signal.aborted)
        setNotice(
          error instanceof Error
            ? error.message
            : "Vox couldn’t receive this request. Try again.",
        );
    } finally {
      unsubscribe();
      if (operation.current === abort) {
        operation.current = null;
        setBusy(false);
      }
    }
  }
  return {
    ask,
    busy,
    notice,
    available: !!call?.available && !!call.state.ownerVerified,
  };
}
