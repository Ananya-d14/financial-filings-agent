"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { batchQuery } from "../lib/api";
import type { ChatMessage, MessageTrace, StreamStatus } from "../lib/types";
import { AnswerView } from "./AnswerView";
import { ThinkingIndicator } from "./ThinkingIndicator";

const SAMPLE_QUESTIONS = [
  "What was NVIDIA's FY2024 R&D expense?",
  "Compare gross margins of MSFT, GOOGL, and AAPL from 2020 to 2024.",
  "Summarize Tesla's 2024 risk factors related to China.",
  "Which mega-cap tech firms grew capex faster than revenue 2022–2024?",
];

export function ChatInterface() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<StreamStatus>("idle");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  const submit = useCallback(
    async (query: string) => {
      if (!query.trim() || status !== "idle") return;

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: query,
      };
      const assistantId = crypto.randomUUID();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        isStreaming: true,
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInput("");
      setStatus("planning");

      try {
        const answer = await batchQuery({ query });

        if (answer) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: answer.markdown,
                    answer,
                    isStreaming: false,
                  }
                : m
            )
          );
        } else {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, isStreaming: false, error: "No answer returned." }
                : m
            )
          );
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, isStreaming: false, error: msg }
              : m
          )
        );
      } finally {
        setStatus("idle");
      }
    },
    [status]
  );

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(input);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: 1, overflowY: "auto", padding: "1rem 0" }}>
        {messages.length === 0 ? (
          <div style={{ textAlign: "center", paddingTop: "3rem" }}>
            <p style={{ color: "var(--muted)", marginBottom: "1.5rem", fontSize: "0.9rem" }}>
              Ask any question about the 20 S&P 500 companies, FY2020–2024.
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", justifyContent: "center" }}>
              {SAMPLE_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => submit(q)}
                  style={{
                    padding: "0.4rem 0.8rem",
                    border: "1px solid var(--border)",
                    borderRadius: "6px",
                    background: "none",
                    color: "var(--fg)",
                    cursor: "pointer",
                    fontSize: "0.83rem",
                    fontFamily: "inherit",
                    maxWidth: "280px",
                    textAlign: "left",
                    lineHeight: 1.4,
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              style={{
                marginBottom: "1.25rem",
                display: "flex",
                flexDirection: msg.role === "user" ? "row-reverse" : "row",
                gap: "0.75rem",
              }}
            >
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: "50%",
                  background: msg.role === "user" ? "var(--accent)" : "var(--border)",
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "0.75rem",
                  color: "var(--fg)",
                }}
              >
                {msg.role === "user" ? "U" : "A"}
              </div>
              <div
                style={{
                  maxWidth: "85%",
                  padding: "0.65rem 0.9rem",
                  borderRadius: "8px",
                  background: msg.role === "user" ? "rgba(79,140,255,0.12)" : "var(--code-bg)",
                  border: `1px solid ${msg.role === "user" ? "rgba(79,140,255,0.2)" : "var(--border)"}`,
                  fontSize: "0.9rem",
                  lineHeight: 1.6,
                }}
              >
                {msg.error ? (
                  <span style={{ color: "#ff6b6b" }}>Error: {msg.error}</span>
                ) : msg.answer && !msg.isStreaming ? (
                  <AnswerView answer={msg.answer} trace={msg.trace} />
                ) : msg.isStreaming ? (
                  <ThinkingIndicator status={status} />
                ) : (
                  <span style={{ color: "var(--fg)" }}>{msg.content}</span>
                )}
              </div>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <div
        style={{
          borderTop: "1px solid var(--border)",
          paddingTop: "1rem",
          display: "flex",
          gap: "0.5rem",
          alignItems: "flex-end",
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={status !== "idle"}
          placeholder="Ask about SEC filings… (Enter to send, Shift+Enter for newline)"
          rows={2}
          style={{
            flex: 1,
            resize: "none",
            background: "var(--code-bg)",
            border: "1px solid var(--border)",
            borderRadius: "6px",
            padding: "0.5rem 0.75rem",
            color: "var(--fg)",
            fontFamily: "inherit",
            fontSize: "0.9rem",
            lineHeight: 1.5,
            outline: "none",
          }}
        />
        <button
          onClick={() => submit(input)}
          disabled={!input.trim() || status !== "idle"}
          style={{
            padding: "0.5rem 1rem",
            background: "var(--accent)",
            border: "none",
            borderRadius: "6px",
            color: "#fff",
            cursor: input.trim() && status === "idle" ? "pointer" : "not-allowed",
            opacity: input.trim() && status === "idle" ? 1 : 0.4,
            fontFamily: "inherit",
            fontSize: "0.9rem",
            height: "2.5rem",
            whiteSpace: "nowrap",
          }}
        >
          {status !== "idle" ? "Thinking…" : "Send →"}
        </button>
      </div>
    </div>
  );
}
