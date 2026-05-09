import { ChatInterface } from "../components/ChatInterface";

export const metadata = {
  title: "Financial Filings Analyst",
  description: "Agentic RAG over SEC filings — grounded, cited, numerically accurate.",
};

export default function HomePage() {
  return (
    <main>
      {/* Bloomberg-style header */}
      <header style={{
        paddingTop: "1rem",
        paddingBottom: "0.75rem",
        borderBottom: "1px solid var(--border)",
        marginBottom: "0.75rem",
        flexShrink: 0,
      }}>
        {/* Top bar: branding + status */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {/* Green terminal dot */}
            <span style={{
              display: "inline-block", width: 8, height: 8,
              borderRadius: "50%", background: "var(--accent)",
              boxShadow: "0 0 6px var(--accent)",
            }} />
            <h1 style={{ margin: 0, fontSize: "1rem", fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--fg)" }}>
              Financial Filings Analyst
            </h1>
            <span style={{ fontSize: "0.7rem", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
              SEC · 10-K / 10-Q / 8-K · FY2020–2024
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span className="tag tag-green">LIVE</span>
            <span className="tag">20 TICKERS</span>
            <span className="tag">5 YRS</span>
          </div>
        </div>

        {/* Ticker strip */}
        <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
          {[
            ["MSFT","blue"],["AAPL","#a8b2c0"],["GOOGL","#4285f4"],["AMZN","#ff9900"],
            ["META","#0866ff"],["NVDA","#76b900"],["TSLA","#cc3333"],["AMD","#ed1c24"],
            ["INTC","#0068b5"],["CRM","#009edb"],["ORCL","#c74634"],["JPM","#003087"],
            ["BAC","#e31837"],["WMT","#0071ce"],["COST","#005daa"],["JNJ","#cc0000"],
            ["PFE","#003aaa"],["CAT","#ffcc00"],["XOM","#c8102e"],["LLY","#d4291f"],
          ].map(([ticker, color]) => (
            <span key={ticker} style={{
              fontSize: "0.68rem",
              fontFamily: "var(--font-mono)",
              padding: "0.1rem 0.4rem",
              border: `1px solid ${color}55`,
              borderRadius: "2px",
              color: color as string,
              background: `${color}15`,
              letterSpacing: "0.04em",
            }}>
              {ticker}
            </span>
          ))}
        </div>
      </header>

      {/* Chat area */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <ChatInterface />
      </div>
    </main>
  );
}
