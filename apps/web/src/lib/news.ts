import type { NewsVerification } from "@navox/contracts";

export const newsStatus: Record<NewsVerification, string> = {
  VERIFIED: "Verified",
  CORROBORATED: "Verified by multiple sources",
  ATTRIBUTED: "Source statement",
  DEVELOPING: "Developing",
  UNCONFIRMED: "Not confirmed",
  DISPUTED: "Sources disagree",
  CONTRADICTED: "Contradicted by evidence",
  RETRACTED: "Retracted",
};

export const newsStatusExplanation: Record<NewsVerification, string> = {
  VERIFIED: "Supported by reviewed primary evidence.",
  CORROBORATED:
    "Supported by independent reporting. Copies do not count as extra confirmation.",
  ATTRIBUTED: "This is an attributed statement, not independent confirmation.",
  DEVELOPING: "Details are still being confirmed.",
  UNCONFIRMED: "There is not enough reviewed evidence to confirm this report.",
  DISPUTED: "Credible sources disagree on a material claim.",
  CONTRADICTED:
    "Reviewed evidence contradicts a material claim in this report.",
  RETRACTED: "The originating source has withdrawn a material claim.",
};

export function safeNewsUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export class NewsRequestError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function newsRequest<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const base =
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
  const response = await fetch(`${base}/news${path}`, {
    ...options,
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const message =
      response.status === 401
        ? "Sign in to see your news."
        : response.status === 404
          ? "This story is no longer available."
          : response.status === 429
            ? "News was refreshed recently. Please try again shortly."
            : "We couldn’t refresh your news. Please try again.";
    throw new NewsRequestError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export function newsTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Time unavailable"
    : date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}

export const newsUpdateLabels: Record<string, string> = {
  DISCOVERED: "Story added",
  SOURCE_ADDED: "Another source added",
  SOURCE_UPDATED:
    "A source changed its report; evidence is being checked again",
  VERIFICATION_CHANGED: "Evidence assessment changed",
};
