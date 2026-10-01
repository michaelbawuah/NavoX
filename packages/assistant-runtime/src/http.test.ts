import { describe, expect, it } from "vitest";
import type { AssistantError } from "./errors";
import { assertSameOriginMutation, httpOriginOf } from "./http";

const base = {
  method: "POST",
  origin: "https://navox.example",
  requestUrl: "https://navox.example/api/v1/assistant/sessions",
  secFetchSite: "same-origin",
  contentType: "application/json",
  configuredOrigin: null,
};

function failureOf(
  input: Parameters<typeof assertSameOriginMutation>[0],
): AssistantError {
  try {
    assertSameOriginMutation(input);
    throw new Error("expected the guard to refuse this request");
  } catch (error) {
    return error as AssistantError;
  }
}

describe("httpOriginOf", () => {
  it("normalises http(s) origins and folds default ports", () => {
    expect(httpOriginOf("https://navox.example")).toBe("https://navox.example");
    expect(httpOriginOf("https://navox.example:443")).toBe(
      "https://navox.example",
    );
    expect(httpOriginOf("http://localhost:3000")).toBe("http://localhost:3000");
    expect(httpOriginOf("https://navox.example/path?q=1")).toBe(
      "https://navox.example",
    );
  });

  it("refuses malformed and non-http values", () => {
    for (const value of [
      "",
      "not a url",
      "null",
      "//navox.example",
      "ftp://navox.example",
      "javascript:alert(1)",
    ]) {
      expect(httpOriginOf(value)).toBeNull();
    }
    expect(httpOriginOf(null)).toBeNull();
    expect(httpOriginOf(undefined)).toBeNull();
  });
});

describe("same-origin mutation guard", () => {
  it("allows an exact same-origin JSON mutation", () => {
    expect(() => assertSameOriginMutation(base)).not.toThrow();
    expect(() =>
      assertSameOriginMutation({
        ...base,
        contentType: "application/json; charset=utf-8",
      }),
    ).not.toThrow();
    expect(() =>
      assertSameOriginMutation({ ...base, secFetchSite: null }),
    ).not.toThrow();
    expect(() =>
      assertSameOriginMutation({
        ...base,
        origin: "https://navox.example:443",
      }),
    ).not.toThrow();
  });

  it("refuses a missing or cross-host origin", () => {
    expect(failureOf({ ...base, origin: null }).code).toBe("forbidden");
    expect(failureOf({ ...base, origin: "https://evil.example" }).code).toBe(
      "forbidden",
    );
  });

  it("refuses a same-host origin with a different scheme or port", () => {
    // The classic spoof: same host name, downgraded or upgraded scheme.
    expect(failureOf({ ...base, origin: "http://navox.example" }).code).toBe(
      "forbidden",
    );
    expect(
      failureOf({ ...base, origin: "https://navox.example:8443" }).code,
    ).toBe("forbidden");
    expect(
      failureOf({
        ...base,
        origin: "https://navox.example",
        requestUrl: "https://navox.example:8443/api/v1/assistant/sessions",
      }).code,
    ).toBe("forbidden");
  });

  it("refuses malformed and non-http origins", () => {
    for (const origin of [
      "",
      "not a url",
      "null",
      "//navox.example",
      "ftp://navox.example",
    ]) {
      expect(failureOf({ ...base, origin }).code).toBe("forbidden");
    }
    expect(failureOf({ ...base, origin: "javascript:alert(1)" }).code).toBe(
      "forbidden",
    );
  });

  it("refuses a call when the expected origin cannot be established", () => {
    const failure = failureOf({ ...base, requestUrl: "not a url" });
    expect(failure.code).toBe("misconfigured");
    expect(failure.status).toBe(503);
  });

  it("uses the configured public origin exactly when one is set", () => {
    expect(() =>
      assertSameOriginMutation({
        ...base,
        origin: "https://app.navox.example",
        requestUrl: "http://10.0.0.5:3000/api/v1/assistant/sessions",
        configuredOrigin: "https://app.navox.example",
      }),
    ).not.toThrow();
    expect(
      failureOf({
        ...base,
        origin: "https://app.navox.example",
        configuredOrigin: "http://app.navox.example",
      }).code,
    ).toBe("forbidden");
    expect(
      failureOf({
        ...base,
        origin: "https://navox.example",
        configuredOrigin: "https://app.navox.example",
      }).code,
    ).toBe("forbidden");
    expect(failureOf({ ...base, configuredOrigin: "not a url" }).code).toBe(
      "misconfigured",
    );
  });

  it("refuses a cross-site fetch hint even when the origin matches", () => {
    expect(failureOf({ ...base, secFetchSite: "cross-site" }).code).toBe(
      "forbidden",
    );
  });

  it("refuses a non-JSON body", () => {
    expect(failureOf({ ...base, contentType: "text/plain" }).code).toBe(
      "invalid_request",
    );
    expect(failureOf({ ...base, contentType: null }).code).toBe(
      "invalid_request",
    );
  });

  it("does not require an origin for safe reads", () => {
    expect(() =>
      assertSameOriginMutation({
        ...base,
        method: "GET",
        origin: null,
        contentType: null,
        requestUrl: "not a url",
      }),
    ).not.toThrow();
  });
});
