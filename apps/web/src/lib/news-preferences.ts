export function parseFollowedLabels(
  value: FormDataEntryValue | null,
): string[] {
  return String(value ?? "")
    .split(",")
    .map((label) => label.trim())
    .filter(Boolean);
}
