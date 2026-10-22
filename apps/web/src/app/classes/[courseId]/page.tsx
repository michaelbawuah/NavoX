import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountWorkspace } from "../../../components/account-workspace";
import { productFeatures } from "../../../lib/product-features";
import { readCourseMaterialReference } from "../../../lib/student-material-link";

export const metadata: Metadata = { title: "Your course · NavoX" };

export default async function CoursePage({
  params,
  searchParams,
}: {
  params: Promise<{ courseId: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  if (!productFeatures.studentCoursework) notFound();
  const { courseId } = await params;
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      courseId,
    )
  )
    notFound();
  const materialReference = readCourseMaterialReference(
    (await searchParams) ?? {},
  );
  return (
    <AccountWorkspace
      view="course"
      courseId={courseId}
      materialReference={materialReference}
    />
  );
}
