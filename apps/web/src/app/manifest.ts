import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "NavoX",
    short_name: "NavoX",
    description: "Your classes and your day, together.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#122124",
    theme_color: "#122124",
    icons: [
      {
        src: "/icons/navox-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/navox-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/navox-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
