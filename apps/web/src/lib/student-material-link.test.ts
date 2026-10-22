import { describe, expect, it } from "vitest";
import {
  courseMaterialHref,
  readCourseMaterialReference,
} from "./student-material-link";

const course = "11111111-1111-4111-8111-111111111111";
const material = "22222222-2222-4222-8222-222222222222";
describe("saved material source links", () => {
  it("round-trips the exact saved edition and page without accepting a supplied URL", () => {
    const href = courseMaterialHref(course, {
      materialId: material,
      page: 2,
      revision: 3,
    });
    const url = new URL(href, "https://navox.net");
    expect(url.pathname).toBe(`/classes/${course}`);
    expect(
      readCourseMaterialReference(Object.fromEntries(url.searchParams)),
    ).toEqual({
      materialId: material,
      page: 2,
      revision: 3,
    });
  });
  it("ignores incomplete, ambiguous, out-of-range and malformed references", () => {
    const valid = { material, page: "2", revision: "3" };
    for (const value of [
      {},
      { ...valid, material: "https://evil.test/file" },
      { ...valid, material: [material, material] },
      { ...valid, page: ["1", "2"] },
      { ...valid, page: "2suffix" },
      { ...valid, page: "0" },
      { ...valid, page: "101" },
      { ...valid, revision: "0" },
      { ...valid, revision: "2147483648" },
    ])
      expect(readCourseMaterialReference(value)).toBeNull();
    expect(() =>
      courseMaterialHref("//evil.test", {
        materialId: material,
        page: 1,
        revision: 1,
      }),
    ).toThrow();
  });
});
