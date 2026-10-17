"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import styles from "./student-voice-launcher.module.css";

const StudentQuickAsk = dynamic(
  () =>
    import("./student-voice-dialog").then((module) => module.StudentQuickAsk),
  { ssr: false },
);

function Mic() {
  return (
    <svg
      width="19"
      height="19"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <rect
        x="9"
        y="2"
        width="6"
        height="13"
        rx="3"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** The shell may render this on every student screen. Opening never starts the microphone. */
export function StudentVoiceLauncher({ accountKey }: { accountKey?: string }) {
  const [open, setOpen] = useState(false);
  const previousAccount = useRef(accountKey);
  useEffect(() => {
    if (previousAccount.current !== accountKey) {
      previousAccount.current = accountKey;
      setOpen(false);
    }
  }, [accountKey]);
  return (
    <>
      <button
        type="button"
        className={styles.launcher}
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
      >
        <Mic />
        <span>Ask NavoX</span>
      </button>
      {open && (
        <StudentQuickAsk
          key={accountKey ?? "current-account"}
          accountKey={accountKey}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
