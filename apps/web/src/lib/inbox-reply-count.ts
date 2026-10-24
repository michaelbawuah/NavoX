"use client";
import { useEffect, useState } from "react";
import { loadGoogleConnections } from "./account-access";
import { loadOutlookReplySummary } from "./outlook-summary";
import { readHomeItems, replyItems } from "./today-home";

const base =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export function useInboxReplyCount(
  accountKey: string | undefined,
  supplied: number | undefined,
  timezone?: string,
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
        const [google, outlook] = await Promise.allSettled([
          Promise.all([
            loadGoogleConnections(base),
            fetch(
              `${base}/today${timezone ? `?timezone=${encodeURIComponent(timezone)}` : ""}`,
              {
                credentials: "include",
                cache: "no-store",
                headers: { "X-NavoX-Account": accountKey },
                signal: AbortSignal.any([
                  controller.signal,
                  AbortSignal.timeout(12000),
                ]),
              },
            ),
          ]).then(async ([connections, response]) => {
            if (!response.ok) throw Error("Unavailable");
            return replyItems(readHomeItems(await response.json()), connections)
              .length;
          }),
          loadOutlookReplySummary(accountKey, controller.signal),
        ]);
        if (google.status === "rejected" && outlook.status === "rejected")
          throw Error("Unavailable");
        if (!controller.signal.aborted)
          setSnapshot({
            key: accountKey,
            count:
              (google.status === "fulfilled" ? google.value : 0) +
              (outlook.status === "fulfilled"
                ? outlook.value.messages.length
                : 0),
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
    window.addEventListener("focus", visible);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("focus", visible);
    };
  }, [accountKey, supplied, timezone]);
  return (
    supplied ?? (snapshot && snapshot.key === accountKey ? snapshot.count : 0)
  );
}
