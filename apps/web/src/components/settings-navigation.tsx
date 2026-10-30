"use client";
import { useEffect, useRef, useState } from "react";
import styles from "./today-workspace.module.css";

const sections = [
  ["profile", "Profile"],
  ["school-term", "School & term"],
  ["settings-connections", "Connections"],
  ["notifications", "Notifications"],
  ["voice-noa", "Voice & Noa"],
  ["memory-privacy", "Memory & privacy"],
  ["appearance", "Appearance"],
  ["routine-alarm", "Routine & alarm"],
] as const;

export function SettingsNavigation() {
  const [active, setActive] = useState<string>("profile");
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    const update = () => {
      const hash = window.location.hash.slice(1);
      if (hash === "saved-memory") setActive("memory-privacy");
      else if (sections.some(([id]) => id === hash)) setActive(hash);
    };
    update();
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    const nav = navigation.current;
    const selected = nav?.querySelector<HTMLElement>(`a[href="#${active}"]`);
    if (!nav || !selected || nav.scrollWidth <= nav.clientWidth) return;
    const offset =
      selected.getBoundingClientRect().left -
      nav.getBoundingClientRect().left +
      nav.scrollLeft;
    nav.scrollLeft = Math.max(
      0,
      offset - (nav.clientWidth - selected.clientWidth) / 2,
    );
  }, [active]);
  return (
    <nav
      ref={navigation}
      className={styles.settingsNav}
      aria-label="Settings sections"
    >
      {sections.map(([id, label]) => (
        <a
          key={id}
          href={`#${id}`}
          aria-current={active === id ? "location" : undefined}
          onClick={() => setActive(id)}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}
