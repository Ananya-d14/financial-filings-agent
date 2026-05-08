import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Financial Filings Analyst",
  description: "Agentic RAG over SEC filings — grounded, cited, numerically accurate.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
