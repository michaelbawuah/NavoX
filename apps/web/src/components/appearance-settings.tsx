"use client";
import { useAppearance } from "../lib/appearance";
import styles from "./today-workspace.module.css";
export function AppearanceSettings({ accountKey }: { accountKey: string }) {
  const { theme, choose } = useAppearance(accountKey);
  return (
    <section
      className={styles.controlCard}
      aria-labelledby="appearance-heading"
    >
      <h2 id="appearance-heading">Appearance</h2>
      <fieldset style={{ border: 0, padding: 0 }}>
        <legend>Color theme</legend>
        {(["dark", "light"] as const).map((value) => (
          <button
            key={value}
            type="button"
            className="nx-button"
            data-variant="secondary"
            aria-pressed={theme === value}
            onClick={() => choose(value)}
          >
            {value === "dark" ? "Dark" : "Light"}
          </button>
        ))}
      </fieldset>
    </section>
  );
}
