import type { KnowledgeConflict } from "@navox/contracts";
import { safeSourceUrl } from "../lib/search";
import styles from "./knowledge-conflicts.module.css";

function eventTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Time unavailable"
    : new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(date);
}

export function KnowledgeConflicts({
  conflicts,
}: {
  conflicts: KnowledgeConflict[];
}) {
  if (!conflicts.length) return null;
  return (
    <section className={styles.conflicts} aria-label="Sources disagree">
      <h3>Sources give different times</h3>
      {conflicts.map((conflict) => (
        <article
          key={`${conflict.entity_key}-${conflict.values.map((value) => value.resource_id).join("-")}`}
        >
          <p>{conflict.explanation}</p>
          <ul>
            {conflict.values.map((value) => {
              const url = safeSourceUrl(value.canonical_url);
              return (
                <li key={value.resource_id}>
                  <strong>{eventTime(value.value)}</strong>
                  <span>
                    {value.source_type === "CALENDAR_EVENT"
                      ? "Calendar"
                      : "Calendar block in email"}
                    : {value.title ?? "Untitled source"}
                  </span>
                  {value.resource_id === conflict.preferred_resource_id && (
                    <span>Current schedule source</span>
                  )}
                  {value.source_updated_at && (
                    <span>Updated {eventTime(value.source_updated_at)}</span>
                  )}
                  {url ? (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      referrerPolicy="no-referrer"
                    >
                      Open source
                    </a>
                  ) : (
                    <span>Source link unavailable</span>
                  )}
                </li>
              );
            })}
          </ul>
          {!conflict.preferred_resource_id && (
            <p>Refresh the sources before relying on a start time.</p>
          )}
        </article>
      ))}
    </section>
  );
}
