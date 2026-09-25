"use client";

import {
  groupSourceReferences,
  type TodaySource,
} from "../lib/source-references";
import { SourceEvidence } from "./source-evidence";
import styles from "./today-workspace.module.css";

export function SourceReferences({
  sources,
  paused,
  formatDate,
}: {
  sources: TodaySource[];
  paused: boolean;
  formatDate: (value: string) => string;
}) {
  return groupSourceReferences(sources).map(({ key, references: group }) => {
    const first = group[0];
    return (
      <div className={styles.evidenceSource} key={key}>
        <strong>
          {first.provider} · {first.source_type.replaceAll("_", " ")}
        </strong>
        {first.external_resource_id && (
          <small>Source reference: {first.external_resource_id}</small>
        )}
        {group.map((reference, index) => (
          <div
            className={styles.evidenceReference}
            key={
              reference.evidence_id ??
              `${reference.observed_at}:${reference.external_resource_id}`
            }
          >
            {index > 0 && <small>Additional supporting passage</small>}
            {reference.observed_at &&
              (index === 0 || reference.observed_at !== first.observed_at) && (
                <small>Observed {formatDate(reference.observed_at)}</small>
              )}
            {reference.provider === "google" &&
              reference.source_type === "gmail_message" &&
              reference.evidence_id && (
                <SourceEvidence
                  evidenceId={reference.evidence_id}
                  paused={paused}
                />
              )}
          </div>
        ))}
      </div>
    );
  });
}
