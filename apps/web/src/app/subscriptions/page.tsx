"use client";

import { useEffect, useState } from "react";
import { SubscriptionsDashboard } from "../../components/subscriptions-dashboard";
import styles from "../../components/today-workspace.module.css";
import { WorkspaceShell } from "../../components/workspace-shell";

const apiBase =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export default function SubscriptionsPage() {
  const [session, setSession] = useState<
    "loading" | "ready" | "signed-out" | "error"
  >("loading");
  const [workspace, setWorkspace] = useState("");
  useEffect(() => {
    let current = true;
    void fetch(`${apiBase}/auth/me`, {
      credentials: "include",
      cache: "no-store",
    })
      .then(async (response) => {
        if (!current) return;
        if (response.status === 401) {
          setSession("signed-out");
          return;
        }
        if (!response.ok) {
          setSession("error");
          return;
        }
        const account = (await response.json()) as {
          workspace: { name: string };
        };
        if (current) {
          setWorkspace(account.workspace.name);
          setSession("ready");
        }
      })
      .catch(() => {
        if (current) setSession("error");
      });
    return () => {
      current = false;
    };
  }, []);

  return (
    <WorkspaceShell current="Subscriptions" accountName={workspace}>
      {session === "ready" ? (
        <SubscriptionsDashboard />
      ) : (
        <section className={styles.hero}>
          <div>
            {session === "loading" ? (
              <p role="status">Opening your subscriptions…</p>
            ) : session === "signed-out" ? (
              <>
                <h1>Sign in to your workspace.</h1>
                <p>Your subscriptions are private to your account.</p>
                <a href="/">Sign in to NavoX</a>
              </>
            ) : (
              <>
                <h1>We couldn&apos;t reach your workspace.</h1>
                <p>Check your connection, then refresh this page.</p>
                <a href="/">Return to NavoX</a>
              </>
            )}
          </div>
        </section>
      )}
    </WorkspaceShell>
  );
}
