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

      {/* Per-company trend sparklines */}
      {(() => {
        const groups = extractSparklines(answer.claims);
        if (!groups || groups.length === 0) return null;
        return (
          <div style={{ marginTop: "0.75rem", padding: "0.65rem 0.85rem", background: "var(--surface-2)", borderRadius: "6px", borderLeft: "3px solid var(--accent)" }}>
            <div style={{ fontSize: "0.65rem", fontFamily: "var(--font-mono)", color: "var(--muted)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: "0.5rem" }}>
              Revenue Trend
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem" }}>
              {groups.map((g, i) => {
                const first = g.values[0], last = g.values[g.values.length - 1];
                const pct = ((last - first) / Math.abs(first) * 100).toFixed(1);
                const up = last >= first;
                const fmt = (v: number) => v >= 1e9 ? `$${(v/1e9).toFixed(1)}B` : v >= 1e6 ? `$${(v/1e6).toFixed(0)}M` : `$${v.toLocaleString()}`;
                return (
                  <div key={i} style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                    {/* Company + delta */}
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span style={{ fontSize: "0.75rem", fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--fg)" }}>{g.ticker}</span>
                      <span style={{ fontSize: "0.7rem", fontFamily: "var(--font-mono)", color: up ? "#16a34a" : "var(--red)", fontWeight: 600 }}>
                        {up ? "▲" : "▼"} {Math.abs(Number(pct))}%
                      </span>
                      <span style={{ fontSize: "0.68rem", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                        {fmt(first)} → {fmt(last)}
                      </span>
                    </div>
                    {/* Bar sparkline with year labels */}
                    <Sparkline values={g.values} years={g.years} />
                  </div>
                );
              })}
            </div>
          </div>
        );
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

function Sparkline({ values, years }: { values: number[]; years: (number | string)[] }) {
  if (values.length < 2) return null;
  const BAR_W = 22, H = 32, GAP = 4;
  const W = values.length * BAR_W + (values.length - 1) * GAP;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
      <svg width={W} height={H}>
        {values.map((v, i) => {
          const barH = Math.max(4, ((v - min) / range) * (H - 6) + 6);
          const x = i * (BAR_W + GAP);
          const isLast = i === values.length - 1;
          return (
            <rect
              key={i}
              x={x}
              y={H - barH}
              width={BAR_W}
              height={barH}
              rx={2}
              fill={isLast ? "var(--accent)" : "var(--accent-glow)"}
              stroke={isLast ? "var(--accent)" : "var(--accent-dim)"}
              strokeWidth={0.5}
              className="spark-bar"
              style={{ animationDelay: `${i * 0.08}s`, strokeOpacity: 0.4 }}
            />
          );
        })}
      </svg>
      {/* Year labels below bars */}
      <div style={{ display: "flex", gap: `${GAP}px` }}>
        {years.map((yr, i) => (
          <div key={i} style={{ width: BAR_W, textAlign: "center", fontSize: "0.58rem", fontFamily: "var(--font-mono)", color: i === years.length - 1 ? "var(--accent)" : "var(--muted)", whiteSpace: "nowrap" }}>
            {String(yr).replace("FY", "'")}
          </div>
        ))}
      </div>
    </div>
  );
}

interface SparkGroup { ticker: string; values: number[]; years: (number | string)[]; }

function extractSparklines(claims: any[]): SparkGroup[] | null {
  const numeric = claims.filter((c) => c.is_numeric && c.numeric_value != null && Math.abs(c.numeric_value) > 0);
  if (numeric.length < 2) return null;

  // Group by ticker from citations
  const byTicker: Record<string, { value: number; year: number | string }[]> = {};
  for (const c of numeric) {
    const cits = c.citations ?? [];
    const ticker = cits[0]?.ticker ?? "VALUE";
    const year = cits[0]?.fiscal_year ?? "";
    if (!byTicker[ticker]) byTicker[ticker] = [];
    byTicker[ticker].push({ value: Math.abs(c.numeric_value), year });
  }

  const groups: SparkGroup[] = [];
  for (const [ticker, entries] of Object.entries(byTicker)) {
    if (entries.length < 2) continue;
    const sorted = entries.sort((a, b) => Number(a.year) - Number(b.year));
    groups.push({ ticker, values: sorted.map((e) => e.value), years: sorted.map((e) => e.year) });
  }

  // Fallback: if no per-ticker grouping, show all values as one series
  if (groups.length === 0 && numeric.length >= 2) {
    groups.push({
      ticker: "TREND",
      values: numeric.map((c: any) => Math.abs(c.numeric_value)),
      years: numeric.map((_: any, i: number) => i + 1),
    });
  }

  return groups.length > 0 ? groups : null;
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
