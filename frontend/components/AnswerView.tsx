"use client";

import { CitationChip } from "./CitationChip";
import { ReasoningTrace } from "./ReasoningTrace";
import type { Answer, MessageTrace } from "../lib/types";

interface Props {
  answer: Answer;
  trace?: MessageTrace;
}

/**
 * Renders the final answer with:
 * - Markdown body (handled server-side as raw text when react-markdown isn't available;
 *   upgrading to react-markdown in Phase 7 if needed)
 * - Inline citation chips after each numeric claim
 * - Collapsible reasoning trace
 * - "Cite this answer" copy button
 */
export function AnswerView({ answer, trace }: Props) {
  const numericClaims = answer.claims.filter((c) => c.is_numeric && c.citations.length > 0);

  function handleCopy() {
    const md = buildCitedMarkdown(answer);
    navigator.clipboard.writeText(md).catch(() => {});
  }

  return (
    <div style={{ lineHeight: 1.65 }}>
      {/* Markdown answer */}
      <div
        style={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          overflowX: "auto",
        }}
        dangerouslySetInnerHTML={{ __html: renderMarkdown(answer.markdown) }}
      />

      {/* Inline citations for numeric claims */}
      {numericClaims.length > 0 && (
        <div style={{ marginTop: "1rem" }}>
          <div style={{ fontSize: "0.78rem", color: "var(--muted)", marginBottom: "0.4rem", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase" }}>
            Citations
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }}>
            {numericClaims.flatMap((claim, ci) =>
              claim.citations.map((cit, ji) => (
                <CitationChip
                  key={`${ci}-${ji}`}
                  citation={cit}
                  index={ci * 10 + ji}
                />
              ))
            )}
          </div>
        </div>
      )}

      {/* Copy button */}
      <div style={{ marginTop: "0.75rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button
          onClick={handleCopy}
          title="Copy answer with inline citations"
          style={{
            fontSize: "0.75rem",
            padding: "0.2rem 0.6rem",
            border: "1px solid var(--border)",
            borderRadius: "4px",
            background: "none",
            color: "var(--muted)",
            cursor: "pointer",
            fontFamily: "inherit",
          }}
        >
          📋 Cite this answer
        </button>
        <span style={{ fontSize: "0.7rem", color: "var(--muted)" }}>
          Trace ID: <code style={{ fontSize: "0.7rem" }}>{answer.trace_id.slice(0, 8)}…</code>
        </span>
      </div>

      {/* Reasoning trace */}
      {trace && <ReasoningTrace trace={trace} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNumbers(text: string): string {
  // Convert raw numbers like 365817000000.0 USD → $365.8B
  return text.replace(/(\d{10,}(?:\.\d+)?)\s*(USD)?/g, (_match, num, unit) => {
    const n = parseFloat(num);
    if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
    if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
    return unit ? `$${n.toLocaleString()}` : num;
  });
}

function renderMarkdown(md: string): string {
  // Normalize escaped newlines that LLMs sometimes output in JSON strings
  md = md.replace(/\\n/g, "\n");
  // Format large numbers to human-readable (e.g. 60922000000.0 USD → $60.92B)
  md = formatNumbers(md);

  return md
    // code blocks
    .replace(/```[\s\S]*?```/g, (m) => {
      const code = m.slice(3, -3).replace(/^[a-z]+\n/, "");
      return `<pre style="background:var(--code-bg);padding:0.75rem;border-radius:6px;overflow-x:auto;font-size:0.85rem"><code>${escHtml(code)}</code></pre>`;
    })
    // markdown tables
    .replace(/(\|.+\|\n)+/g, renderTable)
    // bold
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // inline code
    .replace(/`([^`]+)`/g, `<code style="background:var(--code-bg);padding:0.1em 0.3em;border-radius:3px">$1</code>`)
    // headers
    .replace(/^### (.+)$/gm, "<h3 style='font-size:1rem;margin:0.75rem 0 0.35rem'>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2 style='font-size:1.1rem;margin:0.9rem 0 0.4rem'>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1 style='font-size:1.25rem;margin:1rem 0 0.5rem'>$1</h1>")
    // bullet lists
    .replace(/^[*-] (.+)$/gm, "<li style='margin-left:1.2rem'>$1</li>")
    // paragraphs (double newline)
    .replace(/\n\n/g, "</p><p style='margin:0.6rem 0'>")
    .replace(/\n/g, "<br />");
}

function renderTable(tableStr: string): string {
  const rows = tableStr.trim().split("\n").filter((r) => !r.match(/^[\|:\-\s]+$/));
  if (rows.length === 0) return tableStr;

  const [header, ...body] = rows;
  const ths = header.split("|").filter(Boolean).map((h) => `<th style="padding:0.35rem 0.75rem;border:1px solid var(--border);background:var(--code-bg)">${escHtml(h.trim())}</th>`).join("");
  const trs = body.map((row) => {
    const tds = row.split("|").filter(Boolean).map((d) => `<td style="padding:0.3rem 0.75rem;border:1px solid var(--border)">${escHtml(d.trim())}</td>`).join("");
    return `<tr>${tds}</tr>`;
  }).join("");

  return `<table style="border-collapse:collapse;width:100%;margin:0.75rem 0;font-size:0.88rem"><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table>`;
}

function escHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildCitedMarkdown(answer: Answer): string {
  let md = answer.markdown + "\n\n---\n\n**Citations**\n\n";
  const numericClaims = answer.claims.filter((c) => c.is_numeric && c.citations.length > 0);
  numericClaims.forEach((claim, i) => {
    claim.citations.forEach((cit, j) => {
      md += `[${i + 1}.${j + 1}] ${cit.ticker} ${cit.form} FY${cit.fiscal_year} — ${cit.section} (${cit.accession_number})\n`;
    });
  });
  return md;
}
