import { notFound } from "next/navigation";
import { SubscriptionsWorkspace } from "../../components/subscriptions-workspace";
import { productFeatures } from "../../lib/product-features";

export default function SubscriptionsPage() {
  if (!productFeatures.subscriptions) notFound();
  return <SubscriptionsWorkspace />;
}
