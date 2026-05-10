import type { Metadata } from "next";
import "./globals.css";
import { BackgroundCanvas } from "../components/BackgroundCanvas";

export const metadata: Metadata = {
  title: "Financial Filings Analyst",
  description: "Agentic RAG over SEC filings — grounded, cited, numerically accurate.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* Aurora color wash behind the canvas */}
        <div className="aurora" aria-hidden="true">
          <div className="aurora-orb aurora-orb-1" />
          <div className="aurora-orb aurora-orb-2" />
          <div className="aurora-orb aurora-orb-3" />
          <div className="aurora-orb aurora-orb-4" />
          <div className="aurora-orb aurora-orb-5" />
        </div>

        {/* Moving geometric shapes */}
        <BackgroundCanvas />

        {children}
      </body>
    </html>
  );
}
