import type { NewsSourceItem, NewsVerification } from "@navox/contracts";
import type { FormEvent, ReactNode, Ref } from "react";
import {
  newsStatus,
  newsStatusExplanation,
  newsTime,
  safeNewsUrl,
} from "../lib/news";
import styles from "./navox-ui.module.css";

export function NavoXNavigation({
  current,
}: {
  current: "Today" | "Assistant" | "News" | "Search" | "Subscriptions";
}) {
  return (
    <nav aria-label="Workspace" className={styles.navigation}>
      {(
        [
          ["Today", "/"],
          ["NavoX", "/#ask-navox"],
          ["Assistant", "/navox"],
          ["News", "/news"],
          ["Search", "/navox/search"],
          ["Subscriptions", "/subscriptions"],
        ] as const
      ).map(([label, url]) => (
        <a
          key={label}
          href={url}
          aria-current={current === label ? "page" : undefined}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}

export function NavoXPageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.pageHeader}>
      <div>
        <p className={styles.eyebrow}>NAVOX / NEWS</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions}
    </header>
  );
}

export function NavoXAskBox({
  id,
  label,
  value,
  placeholder,
  busy = false,
  hint,
  maxLength = 200,
  buttonLabel = "Ask",
  busyLabel = "Checking…",
  inputRef,
  onChange,
  onSubmit,
}: {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  busy?: boolean;
  hint?: string;
  maxLength?: number;
  buttonLabel?: string;
  busyLabel?: string;
  inputRef?: Ref<HTMLInputElement>;
  onChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <form className={styles.askBox} onSubmit={onSubmit} aria-busy={busy}>
      <label className={styles.srOnly} htmlFor={id}>
        {label}
      </label>
      <input
        ref={inputRef}
        id={id}
        maxLength={maxLength}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        disabled={busy}
        aria-describedby={hint ? `${id}-hint` : undefined}
        required
      />
      <button type="submit" disabled={busy || !value.trim()}>
        {busy ? busyLabel : buttonLabel}
      </button>
      {hint && (
        <p id={`${id}-hint`} className={styles.askHint}>
          {hint}
        </p>
      )}
    </form>
  );
}

export function NavoXCard({ children }: { children: ReactNode }) {
  return <article className={styles.card}>{children}</article>;
}

export function NavoXStatus({
  status,
  explain = false,
}: {
  status: NewsVerification;
  explain?: boolean;
}) {
  return (
    <span className={styles.statusBlock}>
      <span className={styles.status} data-state={status}>
        {newsStatus[status] ?? "Not confirmed"}
      </span>
      {explain && (
        <span className={styles.statusExplanation}>
          {newsStatusExplanation[status] ?? newsStatusExplanation.UNCONFIRMED}
        </span>
      )}
    </span>
  );
}

export function NavoXSourceList({ sources }: { sources: NewsSourceItem[] }) {
  return (
    <ul className={styles.sources}>
      {sources.map((source) => {
        const url = safeNewsUrl(source.canonical_url);
        return (
          <li key={source.id}>
            <strong>{source.source_name}</strong>
            {url ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                referrerPolicy="no-referrer"
              >
                {source.headline}
                <span className={styles.srOnly}> (opens in a new tab)</span>
              </a>
            ) : (
              <span>{source.headline}</span>
            )}
            <small>
              Published{" "}
              <time dateTime={source.published_at}>
                {newsTime(source.published_at)}
              </time>
            </small>
          </li>
        );
      })}
    </ul>
  );
}

export function NavoXEmptyState({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.empty}>
      <h2>{title}</h2>
      <div>{children}</div>
    </section>
  );
}

export function NavoXErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className={styles.error} role="alert">
      <p>{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function NavoXSkeleton({
  label = "Loading your news…",
}: {
  label?: string;
}) {
  return (
    <div role="status" className={styles.skeleton}>
      <p>{label}</p>
      <span aria-hidden="true" />
      <span aria-hidden="true" />
    </div>
  );
}
