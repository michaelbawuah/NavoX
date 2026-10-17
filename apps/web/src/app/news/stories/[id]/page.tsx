import { notFound } from "next/navigation";
import { NewsStoryWorkspace } from "../../../../components/news-workspace";
import { productFeatures } from "../../../../lib/product-features";

export default async function NewsStoryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  if (!productFeatures.news) notFound();
  const { id } = await params;
  return <NewsStoryWorkspace key={id} storyId={id} />;
}
