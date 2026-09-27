import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NavoX — Your day. In perspective.",
  description:
    "Bring your messages, meetings, and commitments into focus with NavoX, your personal operations workspace.",
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
