"use client";
import { useEffect, useState } from "react";
import { loadGoogleConnections } from "./account-access";
import { readHomeItems, replyItems } from "./today-home";

const base =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export function useInboxReplyCount(
  accountKey: string | undefined,
  supplied: number | undefined,
) {
  const [snapshot, setSnapshot] = useState<{
    key: string;
    count: number;
  } | null>(null);
  useEffect(() => {
    if (!accountKey || supplied !== undefined) return;
    const controller = new AbortController();
    let loading = false;
    const refresh = async () => {
      if (loading || document.visibilityState === "hidden") return;
      loading = true;
      try {
        const [connections, response] = await Promise.all([
          loadGoogleConnections(base),
          fetch(`${base}/today`, {
            credentials: "include",
            cache: "no-store",
            headers: { "X-NavoX-Account": accountKey },
            signal: AbortSignal.any([
              controller.signal,
              AbortSignal.timeout(12000),
            ]),
          }),
        ]);
        if (!response.ok) throw Error("Unavailable");
        const items = readHomeItems(await response.json());
        if (!controller.signal.aborted)
          setSnapshot({
            key: accountKey,
            count: replyItems(items, connections).length,
          });
      } catch {
        if (!controller.signal.aborted) setSnapshot(null);
      } finally {
        loading = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 60000);
    const visible = () => void refresh();
    document.addEventListener("visibilitychange", visible);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [accountKey, supplied]);
  return (
    supplied ?? (snapshot && snapshot.key === accountKey ? snapshot.count : 0)
  );
}
