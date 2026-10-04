import type { Metadata } from "next";
import { AccountWorkspace } from "../components/account-workspace";
import { publicSite } from "../lib/public-site";

export const metadata: Metadata = {
  alternates: { canonical: "/" },
  robots: { index: true, follow: true },
  openGraph: {
    type: "website",
    url: publicSite.url,
    siteName: publicSite.name,
    title: publicSite.title,
    description: publicSite.description,
  },
  twitter: {
    card: "summary",
    title: publicSite.title,
    description: publicSite.description,
  },
};

const website = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: publicSite.name,
  url: publicSite.url,
  description: publicSite.description,
};

export default function Home() {
  return (
    <>
      <script
        type="application/ld+json"
        // biome-ignore lint/security/noDangerouslySetInnerHtml: Static site identity with HTML characters escaped for JSON-LD.
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(website).replace(/</g, "\\u003c"),
        }}
      />
      <AccountWorkspace />
    </>
  );
}
