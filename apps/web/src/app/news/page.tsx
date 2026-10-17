import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { NewsWorkspace } from "../../components/news-workspace";
import { productFeatures } from "../../lib/product-features";

export const metadata: Metadata = {
  title: "News · NavoX",
  description:
    "Follow the story, see the sources, and know what is still unclear.",
};

export default function NewsPage() {
  if (!productFeatures.news) notFound();
  return <NewsWorkspace />;
}
