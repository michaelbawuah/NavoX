const FORBIDDEN_PERMISSIONS = new Set([
  "cookies",
  "debugger",
  "declarativeNetRequest",
  "scripting",
  "tabs",
  "webNavigation",
  "webRequest",
]);

export function normalizeOrigin(value, label) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${label} must be an absolute URL`);
  }
  if (url.pathname !== "/" || url.search || url.hash || url.username || url.password) {
    throw new Error(`${label} must contain only scheme, host, and optional port`);
  }
  const isLocal = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && isLocal)) {
    throw new Error(`${label} must use HTTPS unless it is a local development origin`);
  }
  return url.origin;
}

export function manifestFor(template, apiOrigin) {
  const manifest = JSON.parse(template.replace("__NAVOX_API_HOST__", apiOrigin));
  if (manifest.manifest_version !== 3) {
    throw new Error("NavoX extension must use Manifest V3");
  }
  const permissions = new Set(manifest.permissions ?? []);
  for (const permission of permissions) {
    if (FORBIDDEN_PERMISSIONS.has(permission)) {
      throw new Error(`Forbidden extension permission: ${permission}`);
    }
  }
  if (JSON.stringify(manifest.permissions) !== JSON.stringify(["sidePanel", "storage", "activeTab"])) {
    throw new Error("Extension permissions must match the reviewed page-note boundary");
  }
  const hosts = manifest.host_permissions ?? [];
  if (hosts.length !== 1 || hosts[0].includes("<all_urls>") || hosts[0].includes("*://*")) {
    throw new Error("Extension must request exactly one NavoX API host permission");
  }
  if ("content_scripts" in manifest) {
    throw new Error("Milestone 8 must not include content scripts");
  }
  return manifest;
}

export function assertNoRemoteExecutableCode(files) {
  const forbidden = [
    /<script[^>]+src=["']https?:\/\//i,
    /import\s*\(\s*["']https?:\/\//i,
    /from\s+["']https?:\/\//i,
    /eval\s*\(/,
    /new\s+Function\s*\(/,
  ];
  for (const [name, content] of files) {
    for (const pattern of forbidden) {
      if (pattern.test(content)) {
        throw new Error(`Remote or dynamic executable code is not allowed: ${name}`);
      }
    }
  }
}
