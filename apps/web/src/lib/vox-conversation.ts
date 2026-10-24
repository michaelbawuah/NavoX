"use client";
import { TYPED_MESSAGE_LIMIT } from "@navox/contracts";
import { useEffect, useRef, useState } from "react";
import type { VoxCallState } from "./vox-call";

export type VoxTranscriptTurn = {
  question: string;
  answer: string;
  kind: "dialogue" | "summary";
};
const base =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export function readVoxTranscript(value: unknown): {
  revision: number;
  turns: VoxTranscriptTurn[] | null;
} {
  if (!value || typeof value !== "object") throw Error("Invalid conversation");
  const v = value as { revision?: unknown; turns?: unknown };
  if (!Number.isSafeInteger(v.revision) || Number(v.revision) < 0)
    throw Error("Invalid conversation");
  if (
    v.turns !== null &&
    (!Array.isArray(v.turns) ||
      v.turns.length > 1000 ||
      v.turns.some(
        (t) =>
          !t ||
          typeof t !== "object" ||
          typeof t.question !== "string" ||
          typeof t.answer !== "string" ||
          t.question.length > 16000 ||
          t.answer.length > 16000 ||
          !["dialogue", "summary"].includes(t.kind),
      ))
  )
    throw Error("Invalid conversation");
  return v as { revision: number; turns: VoxTranscriptTurn[] | null };
}

export function useVoxConversation(state: VoxCallState | undefined) {
  const key =
    state?.ownerVerified &&
    state.owner &&
    state.callId &&
    ["active", "standby", "reconnecting"].includes(state.phase)
      ? `${state.owner.workspaceId}:${state.owner.userId}/${state.callId}`
      : "";
  const [view, setView] = useState<{
    key: string;
    turns: VoxTranscriptTurn[];
    draft: string;
    notice: string;
    busy: boolean;
    pending: string;
  }>({
    key: "",
    turns: [],
    draft: "",
    notice: "",
    busy: false,
    pending: "",
  });
  const current = useRef(key);
  current.current = key;
  const lifetime = useRef<AbortController | null>(null);
  const retry = useRef<{ text: string; id: string } | null>(null);
  const revision = useRef(-1);
  const pendingAt = useRef(0);
  const pendingRevision = useRef(-1);
  useEffect(() => {
    const abort = new AbortController();
    lifetime.current = abort;
    revision.current = -1;
    retry.current = null;
    pendingAt.current = 0;
    setView({
      key,
      turns: [],
      draft: "",
      notice: "",
      busy: false,
      pending: "",
    });
    if (!key) return () => abort.abort();
    let timer: ReturnType<typeof setTimeout>;
    const [owner, callId] = key.split("/");
    async function poll() {
      try {
        if (document.visibilityState !== "visible") return;
        const r = await fetch(
          `${base}/vox/calls/${callId}/transcript?after=${Math.max(0, revision.current)}`,
          {
            credentials: "include",
            cache: "no-store",
            headers: { "X-NavoX-Account": owner },
            signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]),
          },
        );
        if (!r.ok) {
          if ([401, 403, 404, 409, 410].includes(r.status)) {
            if (current.current === key && !abort.signal.aborted)
              setView((v) => ({
                ...v,
                turns: [],
                pending: "",
                draft: "",
                notice: "This conversation is no longer available.",
              }));
            return;
          }
          throw Error("unavailable");
        }
        const data = readVoxTranscript(await r.json());
        if (abort.signal.aborted || current.current !== key) return;
        revision.current = data.revision;
        setView((v) => {
          if (v.key !== key) return v;
          const turns = data.turns ?? v.turns;
          const replied =
            !!v.pending &&
            data.revision > pendingRevision.current &&
            turns.some(
              (t) => t.kind === "dialogue" && t.question === v.pending,
            );
          const delayed = !!v.pending && Date.now() - pendingAt.current > 90000;
          if (replied) pendingAt.current = 0;
          return {
            ...v,
            turns,
            pending: replied || delayed ? "" : v.pending,
            draft: delayed ? v.pending : v.draft,
            notice: delayed
              ? "A reply hasn’t arrived. Check the connection, then try again."
              : replied
                ? ""
                : v.notice,
          };
        });
      } catch {
        if (!abort.signal.aborted && current.current === key)
          setView((v) => ({
            ...v,
            notice: "Conversation text couldn’t load. Retrying…",
          }));
      } finally {
        if (!abort.signal.aborted) timer = setTimeout(() => void poll(), 1500);
      }
    }
    void poll();
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [key]);
  const visible =
    view.key === key && key
      ? view
      : { key, turns: [], draft: "", notice: "", busy: false, pending: "" };
  function setDraft(draft: string) {
    if (key) setView((v) => (v.key === key ? { ...v, draft } : v));
  }
  async function send(text = visible.draft) {
    const question = text.trim(),
      abort = lifetime.current;
    if (
      !key ||
      !question ||
      [...question].length > TYPED_MESSAGE_LIMIT ||
      visible.busy ||
      visible.pending ||
      !abort ||
      abort.signal.aborted ||
      !["active", "standby"].includes(state?.phase ?? "")
    )
      return;
    const [owner, callId] = key.split("/");
    const id =
      retry.current?.text === question ? retry.current.id : crypto.randomUUID();
    retry.current = { text: question, id };
    setView((v) => ({ ...v, busy: true, notice: "" }));
    try {
      const r = await fetch(`${base}/vox/calls/${callId}/messages`, {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-NavoX-Account": owner,
        },
        body: JSON.stringify({ request_id: id, text: question }),
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(12000)]),
      });
      if (!r.ok) throw Error("unavailable");
      const result = await r.json();
      if (
        result.request_id !== id ||
        result.delivery !== "queued" ||
        result.status !== "accepted"
      )
        throw Error("invalid");
      if (abort.signal.aborted || current.current !== key) return;
      retry.current = null;
      pendingAt.current = Date.now();
      pendingRevision.current = revision.current;
      setView((v) => ({
        ...v,
        draft: "",
        busy: false,
        pending: question,
        notice: "",
      }));
    } catch {
      if (!abort.signal.aborted && current.current === key)
        setView((v) => ({
          ...v,
          busy: false,
          notice:
            "Couldn’t confirm your message. Try again when Vox reconnects.",
        }));
    }
  }
  return {
    ...visible,
    available: !!key && ["active", "standby"].includes(state?.phase ?? ""),
    setDraft,
    send,
  };
}
export type VoxConversationModel = ReturnType<typeof useVoxConversation>;
