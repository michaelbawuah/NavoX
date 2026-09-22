import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  assertNoRemoteExecutableCode,
  manifestFor,
  normalizeOrigin,
} from "./build-lib.mjs";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");
const apiOrigin = normalizeOrigin(
  process.env.NAVOX_EXTENSION_API_ORIGIN ?? "http://localhost:8000",
  "NAVOX_EXTENSION_API_ORIGIN",
);
const webOrigin = normalizeOrigin(
  process.env.NAVOX_EXTENSION_WEB_ORIGIN ?? "http://localhost:3000",
  "NAVOX_EXTENSION_WEB_ORIGIN",
);
const template = await readFile(resolve(root, "manifest.template.json"), "utf8");
const manifest = manifestFor(template, apiOrigin);
const sourceNames = ["presentation.js", "service-worker.js", "sidepanel.js"];
const sourceFiles = await Promise.all(
  sourceNames.map(async (name) => [
    name,
    await readFile(resolve(root, "src", name), "utf8"),
  ]),
);
const html = await readFile(resolve(root, "sidepanel.html"), "utf8");
assertNoRemoteExecutableCode([...sourceFiles, ["sidepanel.html", html]]);

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await writeFile(
  resolve(dist, "manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
);
await writeFile(
  resolve(dist, "config.js"),
  `export const API_BASE_URL = ${JSON.stringify(`${apiOrigin}/api/v1`)};\nexport const WEB_APP_URL = ${JSON.stringify(webOrigin)};\n`,
);
await cp(resolve(root, "sidepanel.html"), resolve(dist, "sidepanel.html"));
await cp(resolve(root, "src", "sidepanel.css"), resolve(dist, "sidepanel.css"));
for (const name of sourceNames) {
  await cp(resolve(root, "src", name), resolve(dist, name));
}
console.log(`Built NavoX Chrome extension for ${apiOrigin}`);
