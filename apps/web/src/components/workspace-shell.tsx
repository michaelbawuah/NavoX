"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  notifyAccountSessionChanged,
  notifyAccountSessionChanging,
} from "../lib/account-session";
import { useAppearance } from "../lib/appearance";
import { useInboxReplyCount } from "../lib/inbox-reply-count";
import { productFeatures } from "../lib/product-features";
import { readStudentProfile, studentRequest } from "../lib/student-schedule";
import {
  STUDENT_TIMEZONE_CHANGED,
  studentLocalTime,
} from "../lib/student-time";
import { useVoxCall } from "./vox-call-provider";
import styles from "./workspace-shell.module.css";

export type WorkspaceDestination =
  | "Today"
  | "Classes"
  | "Assistant"
  | "Inbox"
  | "Planner"
  | "Board"
  | "News"
  | "Subscriptions"
  | "Search"
  | "Connections"
  | "Settings";

const destinations: { label: WorkspaceDestination; href: string }[] = [
  { label: "Today", href: "/" },
  { label: "Classes", href: "/classes" },
  { label: "Board", href: "/board" },
  { label: "Planner", href: "/planner" },
  { label: "Inbox", href: "/inbox" },
];

const iconPaths: Record<
  WorkspaceDestination | "Menu" | "Close" | "More",
  string
> = {
  Today: "M3 10l9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z",
  Classes: "M3 5h7l2 2 2-2h7v15h-7l-2 2-2-2H3zM12 7v15M6 10h3M15 10h3",
  Assistant: "M20 11a8 8 0 0 1-8 8H5l-3 3V11a10 10 0 0 1 18 0M7 10h8M7 14h5",
  Inbox: "M4 4h16v16H4zM4 12h5l2 3h2l2-3h5",
  Planner: "M4 5h16v16H4zM8 3v4M16 3v4M4 10h16M8 14h3M8 17h6",
  Board: "M3 4h18v13H3zM8 21l4-4 4 4M7 9h5M7 12h9",
  News: "M4 4h13v16H4zM17 8h3v12H4M7 8h7M7 12h3M7 16h7",
  Subscriptions: "M4 6h16v13H4zM4 10h16M7 15h4",
  Search: "M20 20l-5-5M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0",
  Connections:
    "M9 8l-2 2a4 4 0 0 0 6 6l2-2M15 16l2-2a4 4 0 0 0-6-6L9 10M9 15l6-6",
  Settings: "M4 7h16M4 17h16M9 4v6M15 14v6",
  Menu: "M4 6h16M4 12h16M4 18h16",
  Close: "M6 6l12 12M6 18L18 6",
  More: "M5 12h1M11 12h1M17 12h1",
};

function Icon({ name }: { name: keyof typeof iconPaths }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d={iconPaths[name]}
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function drawerFocusControls(navigation: HTMLElement): HTMLElement[] {
  return Array.from(
    navigation.querySelectorAll<HTMLElement>(
      'a[href], button:not(:disabled), summary, [tabindex="0"]',
    ),
  ).filter((control) => {
    const closedDisclosure = control.closest("details:not([open])");
    return (
      control.getClientRects().length > 0 &&
      (!closedDisclosure ||
        closedDisclosure.querySelector(":scope > summary") === control)
    );
  });
}

export function WorkspaceShell({
  current,
  children,
  accountName,
  accountKey,
  onSignOut,
  timezone,
  night = false,
  inboxCount: suppliedInboxCount,
}: {
  current: WorkspaceDestination;
  children: ReactNode;
  accountName?: string;
  accountKey?: string;
  onSignOut?: () => Promise<void> | void;
  timezone?: string;
  night?: boolean;
  inboxCount?: number;
}) {
  const router = useRouter();
  const vox = useVoxCall();
  const owner = vox?.state.ownerVerified ? vox.state.owner : null;
  const verifiedKey =
    accountKey || (owner ? `${owner.workspaceId}:${owner.userId}` : undefined);
  useAppearance(verifiedKey);
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState("");
  const [help, setHelp] = useState(false);
  const [clock, setClock] = useState<Date | null>(null);
  const [studentProfile, setStudentProfile] = useState<{
    owner: string;
    timezone: string;
    school: string | null;
  } | null>(null);
  const savedProfile =
    studentProfile?.owner === verifiedKey ? studentProfile : null;
  const savedZone = savedProfile?.timezone;
  const inboxCount = useInboxReplyCount(
    verifiedKey,
    suppliedInboxCount,
    timezone ?? savedZone ?? undefined,
  );
  const inboxCountLabel = `${inboxCount} possible ${inboxCount === 1 ? "reply" : "replies"} in checked mail`;
  const profile = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    let live = true;
    let request = 0;
    const refresh = () => {
      const turn = ++request;
      if (verifiedKey)
        void studentRequest("/profile", readStudentProfile)
          .then((value) => {
            if (live && turn === request)
              setStudentProfile({
                owner: verifiedKey,
                timezone: value.timezone,
                school: value.school_name,
              });
          })
          .catch(() => {});
    };
    const changed = (event: Event) => {
      if (
        (event as CustomEvent<{ accountKey: string }>).detail?.accountKey ===
        verifiedKey
      )
        refresh();
    };
    refresh();
    window.addEventListener(STUDENT_TIMEZONE_CHANGED, changed);
    return () => {
      live = false;
      window.removeEventListener(STUDENT_TIMEZONE_CHANGED, changed);
    };
  }, [verifiedKey]);
  useEffect(() => {
    const tick = () => setClock(new Date());
    tick();
    const interval = window.setInterval(tick, 30_000);
    return () => window.clearInterval(interval);
  }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        router.push("/navox/search");
      }
      if (event.key === "Escape" && profile.current?.open) {
        profile.current.open = false;
        profile.current.querySelector("summary")?.focus();
      }
    };
    const outside = (event: PointerEvent) => {
      if (
        profile.current &&
        event.target instanceof Node &&
        !profile.current.contains(event.target)
      )
        profile.current.open = false;
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", outside);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", outside);
    };
  }, [router]);
  async function signOut() {
    setLeaving(true);
    setError("");
    try {
      if (onSignOut) await onSignOut();
      else {
        const change = notifyAccountSessionChanging();
        try {
          const apiBase =
            process.env.NEXT_PUBLIC_API_BASE_URL ??
            "http://localhost:8000/api/v1";
          const response = await fetch(`${apiBase}/auth/logout`, {
            method: "POST",
            credentials: "include",
            signal: AbortSignal.timeout(10_000),
          });
          if (!response.ok)
            throw new Error("We couldn’t sign you out. Please try again.");
          router.replace("/");
          router.refresh();
        } finally {
          notifyAccountSessionChanged(change);
        }
      }
    } catch {
      setError("We couldn’t sign you out. Please try again.");
    } finally {
      setLeaving(false);
    }
  }
  let date = "";
  if (clock) {
    const zone =
      timezone ?? savedZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
    try {
      const local = studentLocalTime(clock, zone);
      date = `${local.header} · ${local.clock}`;
    } catch {
      date = "";
    }
  }
  function navItem(label: WorkspaceDestination, href: string) {
    if (label === "Board" && !productFeatures.studentWhiteboard)
      return (
        <button key={label} type="button" disabled title="Board is coming soon">
          <Icon name={label} />
          <span>{label}</span>
        </button>
      );
    return (
      <Link
        key={label}
        href={href}
        aria-current={current === label ? "page" : undefined}
      >
        <Icon name={label} />
        <span>{label}</span>
        {label === "Inbox" && inboxCount > 0 && (
          <span className={styles.badge} title={inboxCountLabel}>
            {inboxCount > 99 ? "99+" : inboxCount}
          </span>
        )}
      </Link>
    );
  }
  return (
    <div className={styles.shell} data-navox-shell data-night={night}>
      <a className={styles.skip} href="#workspace-content">
        Skip to content
      </a>
      <aside className={styles.sidebar}>
        <Link href="/" className={styles.brand} aria-label="NavoX home">
          <span className={styles.brandMark} aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          NavoX
        </Link>
        <nav className={styles.navigation} aria-label="Main navigation">
          {destinations.map(({ label, href }) => navItem(label, href))}
        </nav>
        <div className={styles.sidebarBottom}>
          <details className={styles.account} ref={profile}>
            <summary aria-label="Open profile menu">
              <span className={styles.avatar} aria-hidden="true">
                {accountName?.trim().slice(0, 1).toUpperCase() || "N"}
              </span>
              <span className={styles.accountName}>
                <strong title={accountName}>
                  {accountName?.trim().split(/\s+/)[0] || "Your account"}
                </strong>
                {savedProfile?.school && <small>{savedProfile.school}</small>}
              </span>
              <Icon name="More" />
            </summary>
            <div className={styles.profileMenu}>
              <div className={styles.profileIdentity}>
                <strong>{accountName || "Your account"}</strong>
                {savedProfile?.school && <small>{savedProfile.school}</small>}
              </div>
              <Link
                href="/connections"
                aria-current={current === "Connections" ? "page" : undefined}
              >
                Connections
              </Link>
              <Link
                href="/settings"
                aria-current={current === "Settings" ? "page" : undefined}
              >
                Settings
              </Link>
              <button
                type="button"
                aria-expanded={help}
                onClick={() => setHelp(!help)}
              >
                Help
              </button>
              {help && (
                <p>
                  Tap Vox to talk. Add your schedule in Classes and your tasks
                  in Planner.
                </p>
              )}
              <button
                type="button"
                onClick={() => void signOut()}
                disabled={leaving}
              >
                {leaving ? "Signing out…" : "Sign out"}
              </button>
              {error && (
                <p className={styles.error} role="alert">
                  {error}
                </p>
              )}
            </div>
          </details>
        </div>
      </aside>
      <div className={styles.canvas}>
        <header className={styles.topbar}>
          <h1 className={styles.location}>
            {current === "Assistant" ? "Vox" : current}
          </h1>
          <div className={styles.topbarActions}>
            <Link
              className={styles.search}
              href="/navox/search"
              aria-label="Search"
            >
              <Icon name="Search" />
              <span>Search</span>
              <kbd>⌘K</kbd>
            </Link>
            <time className={styles.date} dateTime={clock?.toISOString()}>
              {date}
            </time>
            <Link
              href="/inbox"
              className={styles.mobileInbox}
              aria-label={
                inboxCount > 0 ? `Inbox, ${inboxCountLabel}` : "Inbox"
              }
            >
              <Icon name="Inbox" />
              {inboxCount > 0 && (
                <span className={styles.badge}>
                  {inboxCount > 99 ? "99+" : inboxCount}
                </span>
              )}
            </Link>
          </div>
        </header>
        <main id="workspace-content" className={styles.content}>
          {children}
        </main>
      </div>
      <nav className={styles.mobileNavigation} aria-label="Mobile navigation">
        {navItem("Today", "/")}
        {navItem("Classes", "/classes")}
        {productFeatures.studentVox ? (
          <span aria-hidden="true" />
        ) : (
          <Link href="/navox">Vox</Link>
        )}
        {navItem("Board", "/board")}
        {navItem("Planner", "/planner")}
      </nav>
    </div>
  );
}
