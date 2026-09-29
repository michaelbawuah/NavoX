import type { Metadata } from "next";
import { NewsWorkspace } from "../../components/news-workspace";

export const metadata: Metadata = {
  title: "News · NavoX",
  description:
    "Follow the story, see the sources, and know what is still unclear.",
};

export default function NewsPage() {
  return <NewsWorkspace />;
}
