import { NewsStoryWorkspace } from "../../../../components/news-workspace";

export default async function NewsStoryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <NewsStoryWorkspace key={id} storyId={id} />;
}
