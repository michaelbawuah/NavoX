/** A source link identifies one saved edition, never a file URL or authorization. */
export interface CourseMaterialReference {
  materialId: string;
  page: number;
  revision: number;
}

export type CourseMaterialHref =
  `/classes/${string}?material=${string}&page=${number}&revision=${number}`;

const uuid = (value: unknown): value is string =>
  typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );

export function readCourseMaterialReference(
  params: Record<string, string | string[] | undefined>,
): CourseMaterialReference | null {
  const { material, page, revision } = params;
  if (
    !uuid(material) ||
    typeof page !== "string" ||
    !/^[1-9]\d{0,2}$/.test(page) ||
    Number(page) > 100 ||
    typeof revision !== "string" ||
    !/^[1-9]\d{0,9}$/.test(revision) ||
    Number(revision) > 2_147_483_647
  )
    return null;
  return {
    materialId: material,
    page: Number(page),
    revision: Number(revision),
  };
}

export function courseMaterialHref(
  courseId: string,
  reference: CourseMaterialReference,
): CourseMaterialHref {
  if (
    !uuid(courseId) ||
    !Number.isInteger(reference.page) ||
    !Number.isInteger(reference.revision) ||
    !readCourseMaterialReference({
      material: reference.materialId,
      page: String(reference.page),
      revision: String(reference.revision),
    })
  )
    throw new Error("This source link is unavailable.");
  return `/classes/${courseId}?material=${reference.materialId}&page=${reference.page}&revision=${reference.revision}`;
}
