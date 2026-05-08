import { ChatInterface } from "../components/ChatInterface";

export const metadata = {
  title: "Financial Filings Analyst",
  description: "Agentic RAG over SEC filings — grounded, cited, numerically accurate.",
};

export default function HomePage() {
  return (
    <main style={{ maxWidth: "900px", margin: "0 auto", padding: "1.5rem 1.5rem 0", height: "100dvh", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <header style={{ paddingBottom: "0.75rem", borderBottom: "1px solid var(--border)", marginBottom: "0.75rem", flexShrink: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <div>
            <h1 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 700 }}>
              Financial Filings Analyst
            </h1>
            <p style={{ margin: "0.2rem 0 0", fontSize: "0.78rem", color: "var(--muted)" }}>
              Agentic RAG over SEC 10-K / 10-Q / 8-K · 20 S&P 500 companies · FY2020–2024
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <span className="tag">MSFT AAPL GOOGL AMZN META</span>
            <span className="tag">NVDA TSLA AMD INTC</span>
            <span className="tag">JPM BAC XOM LLY +8</span>
          </div>
        </div>
      </header>

      {/* Chat — takes remaining height */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <ChatInterface />
      </div>
    </main>
  );
}
