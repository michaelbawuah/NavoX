import type { Metadata } from "next";
import { publicSite } from "../lib/public-site";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(publicSite.url),
  title: publicSite.title,
  description: publicSite.description,
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
