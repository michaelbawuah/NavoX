/** Keep validation builds separate from the running local preview. */
const isolated = process.env.NAVOX_ISOLATED_BUILD === "1";
/** @type {import("next").NextConfig} */
const config = {
  transpilePackages: ["@navox/ui"],
  distDir: isolated ? ".next/spec006-check" : ".next",
};
export default config;
