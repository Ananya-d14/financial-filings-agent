"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  asDoneData,
  asPlanData,
  asReflectionData,
  asSynthesisData,
  asToolCallData,
  asToolResultData,
  streamQuery,
} from "../lib/api";
import type {
  ChatMessage,
  MessageTrace,
  StreamStatus,
  ToolCallTrace,
} from "../lib/types";
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
  const [currentTrace, setCurrentTrace] = useState<MessageTrace | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  const submit = useCallback(
    async (query: string) => {
      if (!query.trim() || status !== "idle") return;

      // Abort any in-flight request
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

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

      const trace: MessageTrace = { tool_calls: [], reflections: [], iterations: 1 };
      setCurrentTrace(trace);

      try {
        for await (const event of streamQuery({ query }, controller.signal)) {
          if (event.type === "plan") {
            const planData = asPlanData(event);
            setStatus("running_tools");
            trace.plan = planData;
            setCurrentTrace({ ...trace });

            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: `Planning ${planData.sub_tasks.length} sub-task${planData.sub_tasks.length !== 1 ? "s" : ""}…` }
                  : m
              )
            );
          } else if (event.type === "tool_call") {
            const d = asToolCallData(event);
            setStatus("running_tools");
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: `Running ${d.tool}: ${d.description}` }
                  : m
              )
            );
          } else if (event.type === "tool_result") {
            const d = asToolResultData(event);
            const tc: ToolCallTrace = {
              tool: d.tool,
              description: trace.tool_calls[d.index]?.description ?? d.tool,
              latency_ms: d.latency_ms,
              summary: d.summary,
              error: d.error,
              success: d.success,
            };
            trace.tool_calls[d.index] = tc;
            setCurrentTrace({ ...trace });
          } else if (event.type === "reflection") {
            const d = asReflectionData(event);
            setStatus("reflecting");
            trace.reflections.push(d);
            setCurrentTrace({ ...trace });
          } else if (event.type === "synthesis") {
            setStatus("synthesizing");
            const d = asSynthesisData(event);
            trace.iterations = d.iterations;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: "Synthesizing final answer…" }
                  : m
              )
            );
          } else if (event.type === "done") {
            const d = asDoneData(event);
            setStatus("done");
            trace.iterations = d.iterations;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      role: "assistant",
                      content: d.answer.markdown,
                      answer: d.answer,
                      trace: { ...trace },
                      isStreaming: false,
                    }
                  : m
              )
            );
            break;
          } else if (event.type === "error") {
            throw new Error(String(event.data.error ?? "Unknown error"));
          }
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          const msg = err instanceof Error ? err.message : String(err);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, isStreaming: false, error: msg }
                : m
            )
          );
        }
      } finally {
        setStatus("idle");
        setCurrentTrace(null);
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
      {/* Message list */}
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
                ) : (
                  <span style={{ color: msg.isStreaming ? "var(--muted)" : "var(--fg)" }}>
                    {msg.content}
                  </span>
                )}
              </div>
            </div>
          ))
        )}

        {/* Live thinking indicator (appears below last message) */}
        {status !== "idle" && status !== "done" && (
          <div style={{ paddingLeft: "2.75rem" }}>
            <ThinkingIndicator status={status} />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
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
          Send →
        </button>
      </div>
    </div>
  );
}
