"use client";

import type { StreamStatus } from "../lib/types";

const STATUS_LABELS: Record<StreamStatus, string> = {
  idle: "",
  planning: "Planning sub-tasks…",
  running_tools: "Running tools…",
  reflecting: "Verifying citations…",
  synthesizing: "Synthesizing answer…",
  done: "",
  error: "",
};

export function ThinkingIndicator({ status }: { status: StreamStatus }) {
  const label = STATUS_LABELS[status];
  if (!label) return null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", color: "var(--muted)", fontSize: "0.85rem", padding: "0.5rem 0" }}>
      <span style={{ display: "inline-flex", gap: "2px" }}>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            style={{
              display: "inline-block",
              width: 5,
              height: 5,
              borderRadius: "50%",
              background: "var(--accent)",
              animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
            }}
          />
        ))}
      </span>
      {label}
      <style>{`
        @keyframes pulse {
          0%, 80%, 100% { opacity: 0.2; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
    </div>
  );
}
