export type ImportFormat = "ics" | "csv" | "json";
export interface ImportPreview {
  preview_hash: string;
  count: number;
  timezone: string;
  examples: { id: string; title: string; resource_type: string }[];
  notes: string;
}
export const MAX_IMPORT_BYTES = 2_000_000;
export function importFormat(name: string, size: number): ImportFormat {
  const extension = name.split(".").at(-1)?.toLowerCase();
  if (!Number.isFinite(size) || size <= 0 || size > MAX_IMPORT_BYTES)
    throw new Error("Choose a UTF-8 text file no larger than 2 MB.");
  if (extension !== "ics" && extension !== "csv" && extension !== "json")
    throw new Error("Choose an .ics, .csv, or .json file.");
  return extension;
}
export function validPreview(value: unknown): ImportPreview {
  if (!value || typeof value !== "object")
    throw new Error("Invalid import preview.");
  const p = value as Partial<ImportPreview>;
  if (
    typeof p.preview_hash !== "string" ||
    !/^[a-f0-9]{64}$/.test(p.preview_hash) ||
    !Number.isInteger(p.count) ||
    (p.count ?? 0) < 1 ||
    (p.count ?? 0) > 1000 ||
    typeof p.timezone !== "string" ||
    typeof p.notes !== "string" ||
    !Array.isArray(p.examples) ||
    p.examples.length > 3 ||
    !p.examples.every(
      (e) =>
        e &&
        typeof e.id === "string" &&
        typeof e.title === "string" &&
        typeof e.resource_type === "string",
    )
  )
    throw new Error("Invalid import preview.");
  return p as ImportPreview;
}
export function canConfirmImport(
  preview: ImportPreview | null,
  consent: boolean,
  pending: boolean,
): boolean {
  return preview !== null && consent && !pending;
}
