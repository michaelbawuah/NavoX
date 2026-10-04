import type { MetadataRoute } from "next";
import { publicSite } from "../lib/public-site";

export default function sitemap(): MetadataRoute.Sitemap {
  return [{ url: publicSite.url }];
}
