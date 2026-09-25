export interface TodaySource {
  provider: string;
  source_type: string;
  external_resource_id: string | null;
  connection_id?: string | null;
  evidence_id?: string | null;
  evidence_locator?: Record<string, unknown> | null;
  observed_at?: string | null;
}

export function groupSourceReferences(
  sources: TodaySource[],
): { key: string; references: TodaySource[] }[] {
  const grouped = new Map<string, TodaySource[]>();
  for (const [index, source] of sources.entries()) {
    // Resource IDs alone are not unique across connected Google accounts.
    // Older API payloads without an account ID stay separate.
    const key =
      source.connection_id && source.external_resource_id
        ? JSON.stringify([
            source.connection_id,
            source.provider,
            source.source_type,
            source.external_resource_id,
          ])
        : `unscoped:${index}`;
    const group = grouped.get(key);
    if (group) group.push(source);
    else grouped.set(key, [source]);
  }
  return [...grouped].map(([key, references]) => ({ key, references }));
}
