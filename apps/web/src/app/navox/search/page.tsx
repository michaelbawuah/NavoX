import type { Metadata } from "next";
import { SearchWorkspace } from "../../../components/search-workspace";

export const metadata: Metadata = {
  title: "Search · NavoX",
  description:
    "Search the sources you have already authorized, with a link back to each record.",
};

export default function SearchPage() {
  return <SearchWorkspace />;
}
