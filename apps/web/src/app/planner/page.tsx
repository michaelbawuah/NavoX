import type { Metadata } from "next";
import { AccountWorkspace } from "../../components/account-workspace";

export const metadata: Metadata = { title: "Planner · NavoX" };

export default function PlannerPage() {
  return <AccountWorkspace view="planner" />;
}
