"use client";

import { useState } from "react";
import type { MessageTrace } from "../lib/types";

const TOOL_ICONS: Record<string, string> = {
  xbrl_sql: "📊",
  filing_retriever: "🔍",
  hybrid_retriever: "🔍",
  calculator: "🔢",
  filing_diff: "📝",
};

function ToolBadge({ tool }: { tool: string }) {
  return (
    <span style={{
      fontSize: "0.7rem",
      padding: "0.1rem 0.4rem",
      borderRadius: "4px",
      background: "var(--border)",
      color: "var(--muted)",
      fontFamily: "ui-monospace, monospace",
    }}>
      {TOOL_ICONS[tool] ?? "⚙"} {tool}
    </span>
  );
}

interface Props {
  trace: MessageTrace;
}

export function ReasoningTrace({ trace }: Props) {
  const [open, setOpen] = useState(false);

  const toolCount = trace.tool_calls.length;
  const reflectionCount = trace.reflections.length;
  const failedReflections = trace.reflections.filter((r) => !r.passed);

  const summary = [
    `${trace.iterations} iteration${trace.iterations !== 1 ? "s" : ""}`,
    `${toolCount} tool call${toolCount !== 1 ? "s" : ""}`,
    failedReflections.length > 0 ? `${failedReflections.length} reflection fix${failedReflections.length !== 1 ? "es" : ""}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div style={{ marginTop: "0.75rem" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.4rem",
          fontSize: "0.78rem",
          color: "var(--muted)",
          background: "none",
          border: "none",
          cursor: "pointer",
          padding: "0.2rem 0",
          fontFamily: "inherit",
        }}
      >
        <span style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s", display: "inline-block" }}>▶</span>
        <span>Reasoning trace</span>
        <span style={{ color: "var(--border)", fontStyle: "italic" }}>{summary}</span>
      </button>

      {open && (
        <div style={{
          marginTop: "0.5rem",
          borderLeft: "2px solid var(--border)",
          paddingLeft: "1rem",
          fontSize: "0.82rem",
          lineHeight: 1.6,
        }}>
          {/* Plan */}
          {trace.plan && (
            <div style={{ marginBottom: "0.75rem" }}>
              <div style={{ color: "var(--accent)", fontWeight: 600, marginBottom: "0.25rem" }}>
                Plan {trace.plan.is_refined ? "(refined)" : ""}
              </div>
              {trace.plan.sub_tasks.map((t, i) => (
                <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", marginBottom: "0.2rem" }}>
                  <span style={{ color: "var(--muted)", minWidth: "1.2rem" }}>{i + 1}.</span>
                  <ToolBadge tool={t.tool} />
                  <span style={{ color: "var(--fg)" }}>{t.description}</span>
                </div>
              ))}
              {trace.plan.rationale && (
                <div style={{ color: "var(--muted)", fontStyle: "italic", marginTop: "0.3rem", fontSize: "0.78rem" }}>
                  {trace.plan.rationale}
                </div>
              )}
            </div>
          )}

          {/* Tool calls */}
          {trace.tool_calls.length > 0 && (
            <div style={{ marginBottom: "0.75rem" }}>
              <div style={{ color: "var(--accent)", fontWeight: 600, marginBottom: "0.25rem" }}>
                Tool calls
              </div>
              {trace.tool_calls.map((tc, i) => (
                <div key={i} style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "0.5rem",
                  marginBottom: "0.3rem",
                  padding: "0.3rem 0.5rem",
                  background: tc.success === false ? "rgba(255,80,80,0.06)" : "var(--code-bg)",
                  borderRadius: "4px",
                }}>
                  <span style={{ color: tc.success === false ? "#ff6b6b" : "var(--muted)", fontSize: "0.85rem" }}>
                    {tc.success === false ? "✗" : "✓"}
                  </span>
                  <div>
                    <ToolBadge tool={tc.tool} />
                    {tc.latency_ms !== undefined && (
                      <span style={{ color: "var(--muted)", fontSize: "0.72rem", marginLeft: "0.4rem" }}>
                        {tc.latency_ms}ms
                      </span>
                    )}
                    <div style={{ color: tc.error ? "#ff6b6b" : "var(--fg)", marginTop: "0.1rem" }}>
                      {tc.error ?? tc.summary}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Reflections */}
          {trace.reflections.length > 0 && (
            <div>
              <div style={{ color: "var(--accent)", fontWeight: 600, marginBottom: "0.25rem" }}>
                Reflection checks
              </div>
              {trace.reflections.map((r, i) => (
                <div key={i} style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "0.5rem",
                  marginBottom: "0.25rem",
                  color: r.passed ? "var(--muted)" : "#ffb347",
                }}>
                  <span>{r.passed ? "✓" : "⚠"}</span>
                  <span>{r.phase === "pre_synthesis" ? "Plan complete" : "Citations verified"}</span>
                  {!r.passed && r.failures.length > 0 && (
                    <span style={{ color: "#ffb347", fontSize: "0.78rem" }}>
                      — {r.failures.slice(0, 2).join("; ")}
                      {r.failures.length > 2 ? ` (+${r.failures.length - 2} more)` : ""}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
