"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { batchQuery } from "../lib/api";
import type { ChatMessage, StreamStatus } from "../lib/types";
import { AnswerView } from "./AnswerView";

const QUESTIONS = [
  { tier: "T1", label: "NVDA FY2024 R&D expense", q: "What was NVIDIA's FY2024 R&D expense?", color: "#76b900" },
  { tier: "T2", label: "Apple FY2023 net income", q: "What was Apple's FY2023 net income?", color: "#a8b2c0" },
  { tier: "T3", label: "MSFT vs AAPL gross margins 2020–2024", q: "Compare gross margins of MSFT and AAPL from 2020 to 2024.", color: "#3b82f6" },
  { tier: "T2", label: "Tesla China risk factors 2024", q: "Summarize Tesla's 2024 risk factors related to China.", color: "#cc3333" },
];

function ThinkingBar() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", padding: "0.6rem 0", color: "var(--muted)", fontSize: "0.8rem", fontFamily: "var(--font-mono)" }}>
      <span style={{ color: "var(--accent)", fontSize: "0.7rem", letterSpacing: "0.08em" }}>ANALYZING</span>
      <span style={{ display: "flex", gap: "3px" }}>
        {[0, 1, 2, 3].map((i) => (
          <span key={i} style={{
            display: "inline-block", width: 3, height: 12,
            background: "var(--accent)",
            borderRadius: "1px",
            animation: `pulse-dot 1.2s ease-in-out ${i * 0.15}s infinite`,
          }} />
        ))}
      </span>
      <span>fetching SEC filings...</span>
    </div>
  );
}

export function ChatInterface() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<StreamStatus>("idle");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  // Auto-focus on load
  useEffect(() => { inputRef.current?.focus(); }, []);

  const submit = useCallback(async (query: string) => {
    if (!query.trim() || status !== "idle") return;

    const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", content: query };
    const assistantId = crypto.randomUUID();
    const assistantMsg: ChatMessage = { id: assistantId, role: "assistant", content: "", isStreaming: true };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput("");
    setStatus("planning");

    try {
      const answer = await batchQuery({ query });
      if (answer) {
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, content: answer.markdown, answer, isStreaming: false } : m
        ));
      } else {
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, isStreaming: false, error: "No answer returned." } : m
        ));
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setMessages((prev) => prev.map((m) =>
        m.id === assistantId ? { ...m, isStreaming: false, error: msg } : m
      ));
    } finally {
      setStatus("idle");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [status]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(input);
    }
  }

  const charCount = input.length;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Messages */}
      <div style={{ flex: 1, overflowY: "auto", padding: "0.5rem 0" }}>
        {messages.length === 0 ? (
          <div style={{ paddingTop: "2rem" }}>
            {/* Hero text */}
            <p style={{ color: "var(--muted)", fontSize: "0.82rem", marginBottom: "1.25rem", fontFamily: "var(--font-mono)" }}>
              <span style={{ color: "var(--accent)" }}>&gt;</span> Query 400+ SEC filings. All answers grounded in XBRL data with verifiable citations.
            </p>

            {/* Sample question cards */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.6rem", maxWidth: "700px" }}>
              {QUESTIONS.map(({ tier, label, q, color }) => (
                <button
                  key={q}
                  onClick={() => submit(q)}
                  style={{
                    background: "var(--surface)",
                    border: `1px solid var(--border)`,
                    borderLeft: `3px solid ${color}`,
                    borderRadius: "4px",
                    padding: "0.6rem 0.75rem",
                    cursor: "pointer",
                    textAlign: "left",
                    transition: "border-color 0.15s, background 0.15s",
                    fontFamily: "inherit",
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = color; (e.currentTarget as HTMLElement).style.background = "var(--surface-2)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = "var(--border)"; (e.currentTarget as HTMLElement).style.borderLeftColor = color; (e.currentTarget as HTMLElement).style.background = "var(--surface)"; }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.2rem" }}>
                    <span style={{ fontSize: "0.65rem", fontFamily: "var(--font-mono)", color, letterSpacing: "0.08em" }}>{tier}</span>
                  </div>
                  <div style={{ fontSize: "0.82rem", color: "var(--fg)", lineHeight: 1.35 }}>{label}</div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div key={msg.id} className="fade-in" style={{ marginBottom: "1rem", display: "flex", flexDirection: msg.role === "user" ? "row-reverse" : "row", gap: "0.6rem", alignItems: "flex-start" }}>
              {/* Avatar */}
              <div style={{
                width: 26, height: 26, borderRadius: "3px", flexShrink: 0,
                background: msg.role === "user" ? "var(--accent)" : "var(--surface-2)",
                border: `1px solid ${msg.role === "user" ? "var(--accent)" : "var(--border)"}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: "0.65rem", fontFamily: "var(--font-mono)",
                color: msg.role === "user" ? "var(--bg)" : "var(--muted)",
                fontWeight: 700,
              }}>
                {msg.role === "user" ? "YOU" : "SYS"}
              </div>

              {/* Bubble */}
              <div style={{
                maxWidth: "88%",
                padding: "0.6rem 0.85rem",
                borderRadius: "4px",
                background: msg.role === "user" ? "transparent" : "var(--surface)",
                border: msg.role === "user" ? "1px solid var(--border)" : "1px solid var(--border)",
                borderLeft: msg.role === "assistant" ? "3px solid var(--accent)" : undefined,
                fontSize: "0.88rem",
                lineHeight: 1.6,
                boxShadow: msg.role === "assistant" && !msg.isStreaming ? "0 0 20px rgba(0,212,170,0.04)" : undefined,
              }}>
                {msg.error ? (
                  <span style={{ color: "var(--red)", fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>
                    ✗ {msg.error}
                  </span>
                ) : msg.answer && !msg.isStreaming ? (
                  <AnswerView answer={msg.answer} trace={msg.trace} />
                ) : msg.isStreaming ? (
                  <ThinkingBar />
                ) : (
                  <span style={{ color: "var(--fg)" }}>{msg.content}</span>
                )}
              </div>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem" }}>
        <div style={{ position: "relative" }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={status !== "idle"}
            placeholder="Query SEC filings... (Enter to send, Shift+Enter for newline)"
            rows={2}
            style={{
              width: "100%",
              resize: "none",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderLeft: "3px solid var(--accent-dim)",
              borderRadius: "4px",
              padding: "0.6rem 5rem 0.6rem 0.75rem",
              color: "var(--fg)",
              fontFamily: "var(--font-sans)",
              fontSize: "0.88rem",
              lineHeight: 1.5,
              outline: "none",
              transition: "border-color 0.15s",
            }}
            onFocus={(e) => { e.target.style.borderColor = "var(--accent)"; e.target.style.borderLeftColor = "var(--accent)"; }}
            onBlur={(e) => { e.target.style.borderColor = "var(--border)"; e.target.style.borderLeftColor = "var(--accent-dim)"; }}
          />

          {/* Char count + send */}
          <div style={{ position: "absolute", right: "0.5rem", bottom: "0.45rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
            {charCount > 0 && (
              <span style={{ fontSize: "0.65rem", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                {charCount}
              </span>
            )}
            <button
              onClick={() => submit(input)}
              disabled={!input.trim() || status !== "idle"}
              title="Send (Enter)"
              style={{
                padding: "0.3rem 0.65rem",
                background: input.trim() && status === "idle" ? "var(--accent)" : "var(--surface-2)",
                border: "none",
                borderRadius: "3px",
                color: input.trim() && status === "idle" ? "var(--bg)" : "var(--muted)",
                cursor: input.trim() && status === "idle" ? "pointer" : "not-allowed",
                fontFamily: "var(--font-mono)",
                fontSize: "0.75rem",
                fontWeight: 700,
                letterSpacing: "0.04em",
                transition: "background 0.15s",
              }}
            >
              {status !== "idle" ? "···" : "↵ RUN"}
            </button>
          </div>
        </div>
        <div style={{ marginTop: "0.3rem", display: "flex", justifyContent: "space-between", fontSize: "0.65rem", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
          <span>MSFT · AAPL · NVDA · TSLA · GOOGL · AMZN · META · AMD · +13 more</span>
          <span>FY2020–2024</span>
        </div>
      </div>
    </div>
  );
}
