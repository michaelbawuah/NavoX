import { describe, expect, it } from "vitest";
import { parseVoxWebLookup } from "./vox-web-results";

const encode = (value: unknown) =>
  new TextEncoder().encode(JSON.stringify(value));
const result = () => ({
  status: "complete",
  text: "Fixture [source]",
  checkedAt: "2026-10-24T00:00:00Z",
  citations: [
    {
      start: 8,
      end: 16,
      title: "Official fixtures",
      url: "https://example.org/fixtures",
    },
  ],
});

describe("Vox public-web results", () => {
  it("accepts bounded cited results and minimal statuses", () => {
    expect(parseVoxWebLookup(encode(result()))).toEqual(result());
    expect(parseVoxWebLookup(encode({ status: "searching" }))).toEqual({
      status: "searching",
    });
    expect(parseVoxWebLookup(encode({ status: "unavailable" }))).toEqual({
      status: "unavailable",
    });
  });
  it.each([
    "javascript:alert(1)",
    "http://example.org",
    "https://127.0.0.1",
    "https://user:pass@example.org",
    "https://box.local",
    "https://example.org:8443",
    "https://example.org/\\evil",
  ])("rejects unsafe source %s", (url) => {
    const value = result();
    value.citations[0].url = url;
    expect(parseVoxWebLookup(encode(value))).toBeNull();
  });
  it("rejects uncited, overlapping, oversized and malformed results", () => {
    expect(
      parseVoxWebLookup(encode({ ...result(), citations: [] })),
    ).toBeNull();
    expect(
      parseVoxWebLookup(
        encode({
          ...result(),
          citations: [...result().citations, ...result().citations],
        }),
      ),
    ).toBeNull();
    expect(parseVoxWebLookup(new Uint8Array(12001))).toBeNull();
    expect(parseVoxWebLookup(new Uint8Array([255]))).toBeNull();
    expect(
      parseVoxWebLookup(encode({ ...result(), checkedAt: "not a time" })),
    ).toBeNull();
  });
});
