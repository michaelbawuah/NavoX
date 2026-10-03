"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef, useState } from "react";
import styles from "./workspace-shell.module.css";

export type WorkspaceDestination =
  | "Today"
  | "Assistant"
  | "Inbox"
  | "Planner"
  | "News"
  | "Subscriptions"
  | "Search"
  | "Connections"
  | "Settings";

const destinations: { label: WorkspaceDestination; href: string }[] = [
  { label: "Today", href: "/" },
  { label: "Assistant", href: "/navox" },
  { label: "Inbox", href: "/inbox" },
  { label: "Planner", href: "/planner" },
  { label: "News", href: "/news" },
  { label: "Subscriptions", href: "/subscriptions" },
];

const iconPaths: Record<
  WorkspaceDestination | "Menu" | "Close" | "More",
  string
> = {
  Today: "M3 10l9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z",
  Assistant: "M20 11a8 8 0 0 1-8 8H5l-3 3V11a10 10 0 0 1 18 0M7 10h8M7 14h5",
  Inbox: "M4 4h16v16H4zM4 12h5l2 3h2l2-3h5",
  Planner: "M4 5h16v16H4zM8 3v4M16 3v4M4 10h16M8 14h3M8 17h6",
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

export function WorkspaceShell({
  current,
  children,
  accountName,
  onSignOut,
}: {
  current: WorkspaceDestination;
  children: ReactNode;
  accountName?: string;
  onSignOut?: () => Promise<void> | void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState("");
  const sidebar = useRef<HTMLElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        window.location.assign("/navox/search");
      }
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const viewport = window.matchMedia("(max-width: 760px)");
    if (!viewport.matches) {
      setMenuOpen(false);
      return;
    }
    const previous = document.activeElement;
    const navigation = sidebar.current;
    navigation?.querySelector<HTMLButtonElement>("button")?.focus();
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !navigation) return;
      const controls = Array.from(
        navigation.querySelectorAll<HTMLElement>(
          'a[href], button:not(:disabled), summary, [tabindex="0"]',
        ),
      ).filter((control) => control.getClientRects().length > 0);
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    const closeOnResize = () => {
      if (!viewport.matches) setMenuOpen(false);
    };
    document.addEventListener("keydown", trapFocus);
    viewport.addEventListener("change", closeOnResize);
    return () => {
      document.removeEventListener("keydown", trapFocus);
      viewport.removeEventListener("change", closeOnResize);
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, [menuOpen]);

  async function signOut() {
    setLeaving(true);
    setError("");
    try {
      if (onSignOut) await onSignOut();
      else {
        const apiBase =
          process.env.NEXT_PUBLIC_API_BASE_URL ??
          "http://localhost:8000/api/v1";
        const response = await fetch(`${apiBase}/auth/logout`, {
          method: "POST",
          credentials: "include",
        });
        if (!response.ok)
          throw new Error("We couldn’t sign you out. Please try again.");
        window.location.assign("/");
      }
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "We couldn’t sign you out. Please try again.",
      );
    } finally {
      setLeaving(false);
    }
  }

  const moreActive = !["Today", "Assistant", "Planner"].includes(current);

  return (
    <div className={styles.shell}>
      <a className={styles.skip} href="#workspace-content">
        Skip to workspace
      </a>
      {menuOpen && (
        <button
          type="button"
          className={styles.backdrop}
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        ref={sidebar}
        id="workspace-navigation"
        className={styles.sidebar}
        data-open={menuOpen}
        {...(menuOpen
          ? {
              role: "dialog",
              "aria-modal": true,
              "aria-label": "Workspace navigation",
            }
          : {})}
      >
        <button
          type="button"
          className={styles.drawerClose}
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        >
          <Icon name="Close" />
        </button>
        <Link href="/" className={styles.brand} aria-label="NavoX home">
          <span className={styles.brandMark} aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          NavoX
        </Link>
        <div className={styles.workspaceLabel}>Your personal workspace</div>
        <nav className={styles.navigation} aria-label="Main navigation">
          {destinations.map(({ label, href }) => (
            <Link
              key={label}
              href={href}
              aria-current={current === label ? "page" : undefined}
              onClick={() => setMenuOpen(false)}
            >
              <Icon name={label} />
              <span>{label}</span>
            </Link>
          ))}
        </nav>
        <div className={styles.sidebarBottom}>
          <nav
            className={styles.secondaryNavigation}
            aria-label="Workspace settings"
          >
            <Link
              href="/connections"
              aria-current={current === "Connections" ? "page" : undefined}
              onClick={() => setMenuOpen(false)}
            >
              <Icon name="Connections" />
              Connections
            </Link>
            <Link
              href="/settings"
              aria-current={current === "Settings" ? "page" : undefined}
              onClick={() => setMenuOpen(false)}
            >
              <Icon name="Settings" />
              Settings
            </Link>
          </nav>
          <details className={styles.account}>
            <summary>
              <span className={styles.avatar} aria-hidden="true">
                {accountName?.trim().slice(0, 1).toUpperCase() || "N"}
              </span>
              <span>{accountName || "Your account"}</span>
              <Icon name="More" />
            </summary>
            <button
              type="button"
              onClick={() => void signOut()}
              disabled={leaving}
            >
              {leaving ? "Signing out…" : "Sign out"}
            </button>
          </details>
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
        </div>
      </aside>
      <div className={styles.canvas} inert={menuOpen || undefined}>
        <header className={styles.topbar}>
          <div className={styles.location}>
            <button
              type="button"
              className={styles.menuButton}
              aria-label={menuOpen ? "Close navigation" : "Open navigation"}
              aria-expanded={menuOpen}
              aria-controls="workspace-navigation"
              onClick={() => setMenuOpen(!menuOpen)}
            >
              <Icon name={menuOpen ? "Close" : "Menu"} />
            </button>
            <span>{current}</span>
          </div>
          <div className={styles.topbarActions}>
            <Link
              className={styles.search}
              href="/navox/search"
              aria-label="Search your workspace"
              aria-current={current === "Search" ? "page" : undefined}
            >
              <Icon name="Search" />
              <span>Search your workspace</span>
              <kbd>⌘ K</kbd>
            </Link>
            {current !== "Assistant" && (
              <Link className={styles.ask} href="/navox">
                <Icon name="Assistant" />
                <span>Ask NavoX</span>
              </Link>
            )}
          </div>
        </header>
        <main id="workspace-content" className={styles.content}>
          {children}
        </main>
      </div>
      <nav
        className={styles.mobileNavigation}
        aria-label="Mobile navigation"
        inert={menuOpen || undefined}
      >
        {destinations
          .filter(({ label }) =>
            ["Today", "Assistant", "Planner"].includes(label),
          )
          .map(({ label, href }) => (
            <Link
              key={label}
              href={href}
              aria-current={current === label ? "page" : undefined}
            >
              <Icon name={label} />
              <span>{label}</span>
            </Link>
          ))}
        <button
          type="button"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-expanded={menuOpen}
          aria-controls="workspace-navigation"
          data-active={moreActive}
        >
          <Icon name="More" />
          <span>More</span>
        </button>
      </nav>
    </div>
  );
}
