"use client";

import { useState } from "react";
import type { Citation } from "../lib/types";

// Color map per ticker, Bloomberg terminal style
const TICKER_COLORS: Record<string, string> = {
  MSFT: "#00a4ef", AAPL: "#a8b2c0", GOOGL: "#4285f4", AMZN: "#ff9900",
  META: "#0866ff", NVDA: "#76b900", TSLA: "#cc3333", AMD: "#ed1c24",
  INTC: "#0068b5", CRM: "#009edb", ORCL: "#c74634", JPM: "#5b8dd9",
  BAC: "#e87070", WMT: "#0071ce", COST: "#005daa", JNJ: "#cc0000",
  PFE: "#3b82f6", CAT: "#ffcc00", XOM: "#c8102e", LLY: "#d4291f",
};

function getTickerColor(ticker: string): string {
  return TICKER_COLORS[ticker?.toUpperCase()] ?? "#64748b";
}

interface Props {
  citation: Citation;
  index: number;
}

export function CitationChip({ citation, index }: Props) {
  const [open, setOpen] = useState(false);
  const color = getTickerColor(citation.ticker);
  const section = citation.item_label ?? citation.section;

  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={`${citation.ticker} ${citation.form} FY${citation.fiscal_year}, ${section}`}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "0.3rem",
          fontSize: "0.68rem",
          padding: "0.15rem 0.5rem",
          borderRadius: "2px",
          border: `1px solid ${color}55`,
          color,
          background: `${color}18`,
          cursor: "pointer",
          verticalAlign: "middle",
          marginLeft: "0.25rem",
          marginBottom: "0.15rem",
          fontFamily: "var(--font-mono)",
          letterSpacing: "0.04em",
          transition: "background 0.15s",
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = `${color}30`; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = `${color}18`; }}
      >
        <span style={{ fontWeight: 700 }}>{citation.ticker}</span>
        <span style={{ opacity: 0.7 }}>{citation.form}</span>
        <span style={{ opacity: 0.5 }}>{citation.fiscal_year}</span>
      </button>

      {open && (
        <span style={{
          position: "absolute",
          bottom: "calc(100% + 6px)",
          left: 0,
          zIndex: 50,
          background: "var(--surface)",
          border: `1px solid ${color}44`,
          borderLeft: `3px solid ${color}`,
          borderRadius: "4px",
          padding: "0.65rem 0.85rem",
          minWidth: "260px",
          maxWidth: "380px",
          fontSize: "0.78rem",
          boxShadow: `0 4px 24px rgba(0,0,0,0.5), 0 0 0 1px ${color}22`,
          lineHeight: 1.55,
        }}>
          {/* Header row */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.4rem" }}>
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color, fontSize: "0.82rem" }}>
              {citation.ticker}
            </span>
            <button onClick={() => setOpen(false)} style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer", fontSize: "0.9rem", lineHeight: 1, padding: 0 }}>×</button>
          </div>

          {/* Filing info */}
          <div style={{ fontFamily: "var(--font-mono)", fontSize: "0.72rem", color: "var(--fg-dim)", marginBottom: "0.3rem" }}>
            <span style={{ color: "var(--fg)" }}>{citation.form}</span>
            {" · "}
            <span>FY{citation.fiscal_year}</span>
            {" · "}
            <span>{section}</span>
          </div>

          {/* Quoted text */}
          {citation.quoted_text ? (
            <div style={{
              borderLeft: `2px solid ${color}55`,
              paddingLeft: "0.5rem",
              color: "var(--muted)",
              fontStyle: "italic",
              fontSize: "0.75rem",
              marginBottom: "0.35rem",
            }}>
              "{citation.quoted_text.slice(0, 220)}{citation.quoted_text.length > 220 ? "…" : ""}"
            </div>
          ) : null}

          {/* Accession + offsets */}
          <div style={{ fontSize: "0.68rem", color: "var(--muted)", fontFamily: "var(--font-mono)", marginTop: "0.3rem", borderTop: "1px solid var(--border)", paddingTop: "0.3rem" }}>
            <span>{citation.accession_number}</span>
            <span style={{ marginLeft: "0.5rem", opacity: 0.6 }}>
              chars {citation.char_offset_start}–{citation.char_offset_end}
            </span>
          </div>

          {/* View on SEC button */}
          <a
            href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${citation.ticker}&type=${citation.form}&dateb=&owner=include&count=10&search_text=`}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.3rem",
              marginTop: "0.5rem",
              padding: "0.25rem 0.6rem",
              background: color + "18",
              border: `1px solid ${color}44`,
              borderRadius: "3px",
              color,
              fontSize: "0.7rem",
              fontFamily: "var(--font-mono)",
              fontWeight: 600,
              textDecoration: "none",
              cursor: "pointer",
              letterSpacing: "0.03em",
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = color + "30"; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = color + "18"; }}
          >
            View on SEC.gov ↗
          </a>
        </span>
      )}
    </span>
  );
}
