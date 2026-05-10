"use client";

import { useState } from "react";
import { CitationChip } from "./CitationChip";
import type { Answer, MessageTrace } from "../lib/types";

interface Props {
  answer: Answer;
  trace?: MessageTrace;
}

export function AnswerView({ answer, trace }: Props) {
  const [copied, setCopied] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);

  const numericClaims = answer.claims.filter((c) => c.is_numeric && c.citations.length > 0);
  const allCitations = answer.claims.flatMap((c) => c.citations);
  const uniqueTickers = [...new Set(allCitations.map((c) => c.ticker).filter(Boolean))];

  function handleCopy() {
    const md = buildCitedMarkdown(answer);
    navigator.clipboard.writeText(md).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div>
      {/* Answer text */}
      <div
        style={{ lineHeight: 1.7, fontSize: "1rem" }}
        dangerouslySetInnerHTML={{ __html: renderMarkdown(answer.markdown) }}
      />

      {/* Sparkline — shown when answer has 2+ numeric claims (time series) */}
      {(() => {
        const spark = extractSparklines(answer.claims);
        return spark && spark.values.length >= 2 ? (
          <div style={{ marginTop: "0.75rem", padding: "0.5rem 0.75rem", background: "var(--surface-2)", borderRadius: "4px", borderLeft: "3px solid var(--accent)", display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <span style={{ fontSize: "0.65rem", fontFamily: "var(--font-mono)", color: "var(--muted)", letterSpacing: "0.08em", textTransform: "uppercase" }}>Trend</span>
            <Sparkline values={spark.values} label={spark.label} />
          </div>
        ) : null;
      })()}

      {/* Citations section */}
      {numericClaims.length > 0 && (
        <div style={{ marginTop: "0.75rem", paddingTop: "0.6rem", borderTop: "1px solid var(--border)" }}>
          <div style={{ fontSize: "0.65rem", color: "var(--accent)", fontFamily: "var(--font-mono)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: "0.4rem" }}>
            Citations · {uniqueTickers.join(" · ")}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.2rem" }}>
            {numericClaims.flatMap((claim, ci) =>
              claim.citations.map((cit, ji) => (
                <CitationChip key={`${ci}-${ji}`} citation={cit} index={ci * 10 + ji} />
              ))
            )}
          </div>
        </div>
      )}

      {/* Footer: tools used + copy + trace ID */}
      <div style={{ marginTop: "0.6rem", display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
        {answer.used_tools && answer.used_tools.length > 0 && (
          <div style={{ display: "flex", gap: "0.25rem" }}>
            {answer.used_tools.map((t) => (
              <span key={t} style={{
                fontSize: "0.62rem",
                fontFamily: "var(--font-mono)",
                padding: "0.1rem 0.35rem",
                border: "1px solid var(--border)",
                borderRadius: "2px",
                color: "var(--muted)",
                background: "var(--surface-2)",
                letterSpacing: "0.04em",
              }}>
                {t.replace("_", " ")}
              </span>
            ))}
          </div>
        )}

        <button
          onClick={handleCopy}
          style={{
            fontSize: "0.68rem",
            padding: "0.15rem 0.5rem",
            border: "1px solid var(--border)",
            borderRadius: "2px",
            background: copied ? "var(--accent-glow)" : "none",
            color: copied ? "var(--accent)" : "var(--muted)",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            transition: "all 0.15s",
          }}
        >
          {copied ? "✓ COPIED" : "COPY"}
        </button>

        {answer.trace_id && (
          <span style={{ fontSize: "0.62rem", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
            ID: {answer.trace_id.slice(0, 8)}
          </span>
        )}

        {/* Reasoning trace toggle */}
        {trace && (trace.tool_calls.length > 0 || trace.plan) && (
          <button
            onClick={() => setTraceOpen((o) => !o)}
            style={{
              fontSize: "0.65rem",
              padding: "0.1rem 0.4rem",
              border: "1px solid var(--border)",
              borderRadius: "2px",
              background: "none",
              color: "var(--muted)",
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
              marginLeft: "auto",
            }}
          >
            {traceOpen ? "▴ TRACE" : "▾ TRACE"}
          </button>
        )}
      </div>

      {/* Collapsible trace */}
      {traceOpen && trace && (
        <div style={{
          marginTop: "0.5rem",
          borderTop: "1px solid var(--border)",
          paddingTop: "0.5rem",
          fontSize: "0.75rem",
          fontFamily: "var(--font-mono)",
          color: "var(--muted)",
        }}>
          {trace.plan && (
            <div style={{ marginBottom: "0.4rem" }}>
              <span style={{ color: "var(--accent)", fontSize: "0.65rem", letterSpacing: "0.08em" }}>PLAN</span>
              {trace.plan.sub_tasks.map((t, i) => (
                <div key={i} style={{ marginTop: "0.15rem", paddingLeft: "0.75rem", borderLeft: "1px solid var(--border)" }}>
                  <span style={{ color: "var(--accent)", opacity: 0.7 }}>[{t.tool}]</span>{" "}
                  <span style={{ color: "var(--fg-dim)" }}>{t.description}</span>
                </div>
              ))}
            </div>
          )}
          {trace.tool_calls.length > 0 && (
            <div>
              <span style={{ color: "var(--accent)", fontSize: "0.65rem", letterSpacing: "0.08em" }}>EXEC</span>
              {trace.tool_calls.map((tc, i) => (
                <div key={i} style={{ marginTop: "0.15rem", paddingLeft: "0.75rem", borderLeft: "1px solid var(--border)", display: "flex", gap: "0.5rem" }}>
                  <span style={{ color: tc.success === false ? "var(--red)" : "var(--accent)", opacity: 0.7 }}>
                    {tc.success === false ? "✗" : "✓"}
                  </span>
                  <span style={{ color: "var(--fg-dim)" }}>{tc.tool}</span>
                  <span style={{ color: "var(--muted)" }}>{tc.latency_ms}ms</span>
                  <span style={{ color: tc.error ? "var(--red)" : "var(--muted)", flex: 1 }}>{tc.error ?? tc.summary}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sparkline SVG component
// ---------------------------------------------------------------------------

function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (values.length < 2) return null;
  const W = 80, H = 28, PAD = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const barW = Math.floor((W - PAD * (values.length - 1)) / values.length);

  return (
    <span style={{ display: "inline-flex", alignItems: "flex-end", gap: "0.5rem", verticalAlign: "middle", marginLeft: "0.4rem" }}>
      <svg width={W} height={H} style={{ display: "block", overflow: "visible" }}>
        {values.map((v, i) => {
          const barH = Math.max(2, ((v - min) / range) * (H - 4) + 4);
          const x = i * (barW + PAD);
          const isLast = i === values.length - 1;
          return (
            <rect
              key={i}
              x={x}
              y={H - barH}
              width={barW}
              height={barH}
              rx={1}
              fill={isLast ? "var(--accent)" : "rgba(0,212,170,0.35)"}
              className="spark-bar"
              style={{ animationDelay: `${i * 0.06}s` }}
            />
          );
        })}
      </svg>
      <span style={{ fontSize: "0.62rem", fontFamily: "var(--font-mono)", color: "var(--accent)", whiteSpace: "nowrap" }}>
        {label}
      </span>
    </span>
  );
}

function extractSparklines(claims: any[]): { label: string; values: number[] } | null {
  const numeric = claims.filter((c) => c.is_numeric && c.numeric_value != null && Math.abs(c.numeric_value) > 0);
  if (numeric.length < 2) return null;
  const values = numeric.map((c) => Math.abs(c.numeric_value as number));
  const fmt = (v: number) => v >= 1e9 ? `$${(v/1e9).toFixed(1)}B` : v >= 1e6 ? `$${(v/1e6).toFixed(1)}M` : `$${v.toLocaleString()}`;
  return { label: `${fmt(values[0])} → ${fmt(values[values.length - 1])}`, values };
}

// ---------------------------------------------------------------------------

function formatNumbers(text: string): string {
  return text.replace(/(\d{10,}(?:\.\d+)?)\s*(USD)?/g, (_m, num) => {
    const n = parseFloat(num);
    if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
    return `$${n.toLocaleString()}`;
  });
}

function renderMarkdown(md: string): string {
  md = md.replace(/\\n/g, "\n");
  md = formatNumbers(md);

  return md
    .replace(/```[\s\S]*?```/g, (m) => {
      const code = m.slice(3, -3).replace(/^[a-z]+\n/, "");
      return `<pre style="background:var(--code-bg);padding:0.75rem;border-radius:4px;overflow-x:auto;border:1px solid var(--border)"><code style="font-family:var(--font-mono);font-size:0.82rem">${escHtml(code)}</code></pre>`;
    })
    .replace(/(\|.+\|[ \t]*\n?)+/g, renderTable)
    .replace(/\*\*(.+?)\*\*/g, "<strong style='color:var(--fg)'>$1</strong>")
    .replace(/`([^`]+)`/g, `<code style="background:var(--code-bg);padding:0.1em 0.3em;border-radius:3px;font-family:var(--font-mono);color:var(--accent);font-size:0.85em">$1</code>`)
    .replace(/^### (.+)$/gm, "<h3 style='font-size:0.9rem;margin:0.75rem 0 0.3rem;color:var(--fg);font-family:var(--font-mono);text-transform:uppercase;letter-spacing:0.06em'>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2 style='font-size:1rem;margin:0.9rem 0 0.4rem;color:var(--fg)'>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1 style='font-size:1.1rem;margin:1rem 0 0.5rem;color:var(--fg)'>$1</h1>")
    .replace(/^[*-] (.+)$/gm, "<div style='margin:0.15rem 0 0.15rem 0.75rem;padding-left:0.5rem;border-left:2px solid var(--border);color:var(--fg-dim)'>$1</div>")
    .replace(/\n\n/g, "<br><br>")
    .replace(/\n/g, "<br>");
}

function renderTable(tableStr: string): string {
  const allRows = tableStr.trim().split("\n");
  // Filter out separator rows (--- lines) and empty lines
  const rows = allRows.filter((r) => r.includes("|") && !r.match(/^[\|:\-\s]+$/));
  if (rows.length === 0) return tableStr;

  // Count expected columns from first row
  const colCount = rows[0].split("|").filter(Boolean).length;

  const [header, ...body] = rows;

  const parseCells = (row: string): string[] => {
    const cells = row.split("|").filter(Boolean).map((c) => c.trim());
    // Pad or trim to expected column count
    while (cells.length < colCount) cells.push("");
    return cells.slice(0, colCount);
  };

  const ths = parseCells(header).map((h) => `<th>${escHtml(h)}</th>`).join("");

  const trs = body.map((row) => {
    const tds = parseCells(row).map((val) => {
      const isNum = /^\$?[\d,\.]+[BMT%]?$/.test(val.replace(/\s/g, "")) && val.length > 0;
      const isEmpty = val === "" || val === "-" || val === "N/A";
      return `<td style="${isNum ? "color:var(--accent);font-family:var(--font-mono);text-align:right" : ""}${isEmpty ? "color:var(--muted)" : ""}">${escHtml(val) || "—"}</td>`;
    }).join("");
    return `<tr>${tds}</tr>`;
  }).join("");

  return `<div style="overflow-x:auto;margin:0.75rem 0"><table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table></div>`;
}

function escHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildCitedMarkdown(answer: Answer): string {
  let md = answer.markdown + "\n\n---\n\n**Source Citations**\n\n";
  answer.claims.filter((c) => c.is_numeric && c.citations.length > 0).forEach((claim, i) => {
    claim.citations.forEach((cit, j) => {
      md += `[${i + 1}.${j + 1}] ${cit.ticker} ${cit.form} FY${cit.fiscal_year} — ${cit.section} (${cit.accession_number})\n`;
    });
  });
  return md;
}
