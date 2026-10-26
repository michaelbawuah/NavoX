"use client";
import { useEffect, useState } from "react";
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
  return (
    <nav className={styles.settingsNav} aria-label="Settings sections">
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
