"use client";
import { useEffect, useState } from "react";
export type Appearance = "dark" | "light";
const eventName = "navox:appearance";
function read(key: string): Appearance {
  try {
    return localStorage.getItem(`navox:appearance:${key}`) === "light"
      ? "light"
      : "dark";
  } catch {
    return "dark";
  }
}
export function useAppearance(accountKey?: string) {
  const [theme, setTheme] = useState<Appearance>("dark");
  useEffect(() => {
    if (!accountKey) return;
    const apply = () => {
      const next = read(accountKey);
      setTheme(next);
      document.documentElement.dataset.navoxTheme = next;
    };
    apply();
    window.addEventListener(eventName, apply);
    window.addEventListener("storage", apply);
    return () => {
      window.removeEventListener(eventName, apply);
      window.removeEventListener("storage", apply);
    };
  }, [accountKey]);
  return {
    theme,
    choose(next: Appearance) {
      if (!accountKey) return;
      try {
        localStorage.setItem(`navox:appearance:${accountKey}`, next);
      } catch {
        /* The current view still supports appearance without storage. */
      }
      setTheme(next);
      document.documentElement.dataset.navoxTheme = next;
      window.dispatchEvent(new Event(eventName));
    },
  };
}
