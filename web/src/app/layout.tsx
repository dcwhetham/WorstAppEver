import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Media Archive",
  description: "Self-hosted media archive dashboard and management tool",
};

export const viewport: Viewport = {
  // Matches the page background so mobile browser chrome does not flash white
  // around a deep-dark UI.
  themeColor: "#05070a",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
