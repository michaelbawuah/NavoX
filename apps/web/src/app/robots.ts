import type { MetadataRoute } from "next";
import { publicSite } from "../lib/public-site";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: "/api/" },
    sitemap: `${publicSite.url}/sitemap.xml`,
  };
}
