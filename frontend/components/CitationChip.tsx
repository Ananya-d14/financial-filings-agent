"use client";

import { useState } from "react";
import type { Citation } from "../lib/types";

interface Props {
  citation: Citation;
  index: number;
}

export function CitationChip({ citation, index }: Props) {
  const [open, setOpen] = useState(false);

  const label = `${citation.ticker} ${citation.form} ${citation.fiscal_year}`;
  const section = citation.item_label ?? citation.section;

  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title={`${label} — ${section}`}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "0.25rem",
          fontSize: "0.7rem",
          padding: "0.1rem 0.45rem",
          borderRadius: "999px",
          border: "1px solid var(--accent)",
          color: "var(--accent)",
          background: "transparent",
          cursor: "pointer",
          verticalAlign: "middle",
          marginLeft: "0.25rem",
          fontFamily: "inherit",
          lineHeight: 1.4,
        }}
      >
        [{index + 1}] {citation.ticker}
      </button>

      {open && (
        <span
          style={{
            position: "absolute",
            bottom: "calc(100% + 6px)",
            left: 0,
            zIndex: 20,
            background: "var(--code-bg)",
            border: "1px solid var(--border)",
            borderRadius: "6px",
            padding: "0.6rem 0.8rem",
            minWidth: "240px",
            maxWidth: "360px",
            fontSize: "0.8rem",
            boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
            lineHeight: 1.5,
          }}
        >
          <span style={{ display: "block", fontWeight: 600, marginBottom: "0.25rem", color: "var(--fg)" }}>
            {label} — {section}
          </span>
          {citation.quoted_text && (
            <span style={{ display: "block", color: "var(--muted)", fontStyle: "italic", borderLeft: "2px solid var(--accent)", paddingLeft: "0.5rem" }}>
              "{citation.quoted_text.slice(0, 200)}{citation.quoted_text.length > 200 ? "…" : ""}"
            </span>
          )}
          <span style={{ display: "block", marginTop: "0.35rem", color: "var(--muted)", fontSize: "0.72rem" }}>
            {citation.accession_number} · offsets {citation.char_offset_start}–{citation.char_offset_end}
          </span>
          <button
            onClick={() => setOpen(false)}
            style={{
              position: "absolute",
              top: "0.3rem",
              right: "0.4rem",
              background: "none",
              border: "none",
              color: "var(--muted)",
              cursor: "pointer",
              fontSize: "1rem",
              lineHeight: 1,
            }}
            aria-label="close"
          >
            ×
          </button>
        </span>
      )}
    </span>
  );
}
