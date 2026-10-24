"use client";

import { useEffect, useRef, useState } from "react";
import {
  canReadOutlook,
  type OutlookConnection,
  type OutlookDraft,
  outlookRequest,
  readOutlookDraft,
} from "../lib/outlook";

const CADENCE = 60_000;
interface Options {
  accountKey: string;
  connection: OutlookConnection;
  drafts: OutlookDraft[];
  editorOpen: boolean;
  onChecked: (draft: OutlookDraft) => void;
}

export function quietReplyCandidates(
  connection: OutlookConnection,
  drafts: OutlookDraft[],
  attempted: ReadonlyMap<string, number> = new Map(),
) {
  if (!canReadOutlook(connection)) return [];
  const freshness = (draft: OutlookDraft) =>
    Math.max(
      Date.parse(draft.reply_checked_at ?? "") || 0,
      attempted.get(draft.id) ?? 0,
    );
  return drafts
    .filter(
      (draft) =>
        draft.connection_id === connection.id &&
        draft.status === "completed" &&
        draft.verified_at &&
        draft.content_available &&
        draft.reply_tracking_ready === true &&
        draft.sent_at &&
        draft.reply_state !== "replied",
    )
    .sort((a, b) => freshness(a) - freshness(b) || a.id.localeCompare(b.id))
    .slice(0, 2);
}

/** A slower list read must not undo newer reply evidence; cleared content always wins. */
export function mergeOutlookReplyReads(
  incoming: OutlookDraft[],
  current: OutlookDraft[],
) {
  return incoming.map((draft) => {
    const previous = current.find((value) => value.id === draft.id);
    if (
      !draft.content_available ||
      !previous?.content_available ||
      draft.status !== "completed" ||
      previous.status !== "completed" ||
      !previous.verified_at
    )
      return draft;
    const previousTime = Date.parse(previous.reply_checked_at ?? "") || 0;
    const nextTime = Date.parse(draft.reply_checked_at ?? "") || 0;
    if (previous.reply_state !== "replied" && previousTime <= nextTime)
      return draft;
    if (draft.reply_state === "replied" && nextTime >= previousTime)
      return draft;
    return {
      ...draft,
      sent_at: previous.sent_at,
      reply_state: previous.reply_state,
      reply_checked_at: previous.reply_checked_at,
      reply_received_at: previous.reply_received_at,
      reply_scan_count: previous.reply_scan_count,
      reply_scan_complete: previous.reply_scan_complete,
      reply_scan_limit: previous.reply_scan_limit,
      reply_notice: previous.reply_notice,
      reply_tracking_ready: previous.reply_tracking_ready,
    };
  });
}

/** While-open, bounded reads. A selected editor and a hidden page both stop work. */
export function useOutlookReplyChecks(options: Options) {
  const [incomplete, setIncomplete] = useState(false);
  const latest = useRef(options);
  const active = useRef<AbortController | null>(null);
  const { accountKey } = options;
  const connectionId = options.connection.id;
  useEffect(() => {
    latest.current = options;
    if (options.editorOpen || !canReadOutlook(options.connection))
      active.current?.abort();
  }, [options]);
  useEffect(() => {
    let stopped = false,
      running = false,
      nextAt = 0;
    let callTimes: number[] = [];
    const attempted = new Map<string, number>();
    const current = () =>
      !stopped &&
      document.visibilityState === "visible" &&
      latest.current.accountKey === accountKey &&
      latest.current.connection.id === connectionId &&
      canReadOutlook(latest.current.connection) &&
      !latest.current.editorOpen;
    const tick = async () => {
      if (!current() || running || Date.now() < nextAt) return;
      const candidates = quietReplyCandidates(
        latest.current.connection,
        latest.current.drafts,
        attempted,
      );
      if (!candidates.length) return;
      nextAt = Date.now() + CADENCE;
      running = true;
      const abort = new AbortController();
      active.current = abort;
      let failed = false;
      try {
        for (const candidate of candidates) {
          if (!current() || abort.signal.aborted) break;
          const fresh = latest.current.drafts.find(
            (draft) => draft.id === candidate.id,
          );
          if (
            !fresh ||
            !quietReplyCandidates(latest.current.connection, [fresh]).length
          )
            continue;
          callTimes = callTimes.filter((time) => time > Date.now() - CADENCE);
          if (callTimes.length >= 2) break;
          callTimes.push(Date.now());
          attempted.set(candidate.id, Date.now());
          try {
            const result = await outlookRequest(
              accountKey,
              `/drafts/${encodeURIComponent(candidate.id)}/check-replies`,
              readOutlookDraft,
              "POST",
              { automatic: true },
              abort.signal,
            );
            if (!current() || abort.signal.aborted) break;
            if (
              result.id !== candidate.id ||
              result.connection_id !== connectionId ||
              result.source_message_id !== candidate.source_message_id ||
              result.status !== "completed" ||
              !result.verified_at
            ) {
              failed = true;
              continue;
            }
            latest.current.onChecked(result);
          } catch {
            if (!abort.signal.aborted) failed = true;
          }
        }
      } finally {
        running = false;
        if (active.current === abort) active.current = null;
        if (current() && !abort.signal.aborted) setIncomplete(failed);
      }
    };
    const visibility = () => {
      if (document.visibilityState !== "visible") active.current?.abort();
      else void tick();
    };
    const timer = window.setInterval(() => {
      void tick();
    }, CADENCE);
    document.addEventListener("visibilitychange", visibility);
    void tick();
    return () => {
      stopped = true;
      active.current?.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [accountKey, connectionId]);
  return incomplete;
}
