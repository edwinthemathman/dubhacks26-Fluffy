import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fluffy",
  description: "Simulated home and Fluffy output",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
