/** Ephemeral cited web results; accepted only from the admitted Vox agent. */
export type VoxWebLookup =
  | { status: "searching" | "unavailable" }
  | {
      status: "complete";
      text: string;
      checkedAt: string;
      citations: { start: number; end: number; title: string; url: string }[];
    };

export function parseVoxWebLookup(bytes: Uint8Array): VoxWebLookup | null {
  if (!bytes.length || bytes.length > 12000) return null;
  try {
    const value = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    );
    if (!value || typeof value !== "object" || Array.isArray(value))
      return null;
    if (value.status === "searching" || value.status === "unavailable")
      return Object.keys(value).length === 1 ? { status: value.status } : null;
    if (
      value.status !== "complete" ||
      typeof value.text !== "string" ||
      !value.text.trim() ||
      Array.from(value.text).length > 5000 ||
      typeof value.checkedAt !== "string" ||
      !Number.isFinite(Date.parse(value.checkedAt)) ||
      !Array.isArray(value.citations) ||
      !value.citations.length ||
      value.citations.length > 6
    )
      return null;
    let previousEnd = 0;
    const length = Array.from(value.text).length;
    const citations = [];
    for (const item of value.citations) {
      if (
        !item ||
        !Number.isInteger(item.start) ||
        !Number.isInteger(item.end) ||
        item.start < previousEnd ||
        item.end <= item.start ||
        item.end > length ||
        typeof item.title !== "string" ||
        !item.title.trim() ||
        item.title.length > 300 ||
        typeof item.url !== "string" ||
        item.url.length > 1500 ||
        /[\s\\]/.test(item.url)
      )
        return null;
      const url = new URL(item.url);
      if (
        url.protocol !== "https:" ||
        url.username ||
        url.password ||
        url.port ||
        !url.hostname.includes(".") ||
        /^\d+(\.\d+){3}$/.test(url.hostname) ||
        /\.(localhost|local|internal)$/.test(url.hostname)
      )
        return null;
      citations.push({
        start: item.start,
        end: item.end,
        title: item.title,
        url: url.href,
      });
      previousEnd = item.end;
    }
    return {
      status: "complete",
      text: value.text,
      checkedAt: value.checkedAt,
      citations,
    };
  } catch {
    return null;
  }
}
