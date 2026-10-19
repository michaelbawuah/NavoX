import { notFound, redirect } from "next/navigation";
import { productFeatures } from "../../lib/product-features";

export default function WhiteboardPage() {
  if (!productFeatures.studentWhiteboard) notFound();
  redirect("/board");
}
