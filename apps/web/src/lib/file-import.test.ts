import { describe, expect, it } from "vitest";
import {
  canConfirmImport,
  importFormat,
  MAX_IMPORT_BYTES,
  validPreview,
} from "./file-import";

const value = {
  preview_hash: "a".repeat(64),
  count: 1,
  timezone: "America/New_York",
  examples: [
    { id: "record-1", title: "Review report", resource_type: "import.csv_row" },
  ],
  notes: "Preview only",
};
describe("file import consent and bounds", () => {
  it.each(["ics", "csv", "json"])("accepts supported %s files", (format) =>
    expect(importFormat(`input.${format.toUpperCase()}`, 10)).toBe(format),
  );
  it.each([0, -1, MAX_IMPORT_BYTES + 1, Number.NaN])(
    "rejects invalid size %s",
    (size) => expect(() => importFormat("x.json", size)).toThrow(),
  );
  it("rejects unsupported extensions", () =>
    expect(() => importFormat("x.json.exe", 10)).toThrow());
  it("requires an explicit checkbox after a valid preview", () => {
    const p = validPreview(value);
    expect(canConfirmImport(null, true, false)).toBe(false);
    expect(canConfirmImport(p, false, false)).toBe(false);
    expect(canConfirmImport(p, true, true)).toBe(false);
    expect(canConfirmImport(p, true, false)).toBe(true);
  });
  it.each([
    { ...value, count: 0 },
    { ...value, count: 1001 },
    { ...value, preview_hash: "changed" },
    { ...value, examples: [{ title: 2, resource_type: "x" }] },
  ])("rejects invalid preview", (input) =>
    expect(() => validPreview(input)).toThrow(),
  );
});
