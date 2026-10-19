import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountWorkspace } from "../../../components/account-workspace";
import { productFeatures } from "../../../lib/product-features";

export const metadata: Metadata = { title: "Your course · NavoX" };

export default async function CoursePage({
  params,
}: {
  params: Promise<{ courseId: string }>;
}) {
  if (!productFeatures.studentCoursework) notFound();
  const { courseId } = await params;
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      courseId,
    )
  )
    notFound();
  return <AccountWorkspace view="course" courseId={courseId} />;
}
