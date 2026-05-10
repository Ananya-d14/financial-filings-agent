import type { Metadata } from "next";
import "./globals.css";
import { BackgroundCanvas } from "../components/BackgroundCanvas";

export const metadata: Metadata = {
  title: "Financial Filings Analyst",
  description: "Agentic RAG over SEC filings, grounded, cited, numerically accurate.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <BackgroundCanvas />
        {children}
      </body>
    </html>
  );
}
