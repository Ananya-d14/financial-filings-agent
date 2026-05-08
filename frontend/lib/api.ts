/**
 * SSE client for the Financial Filings Analyst backend.
 *
 * Usage:
 *   for await (const event of streamQuery("What was NVDA revenue?")) {
 *     if (event.type === "done") { ... }
 *   }
 */

import type {
  Answer,
  DoneEventData,
  PlanEventData,
  ReflectionEventData,
  StreamEvent,
  SynthesisEventData,
  ToolCallEventData,
  ToolResultEventData,
} from "./types";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export interface QueryPayload {
  query: string;
  ticker_filters?: string[];
  year_filters?: number[];
}

// ---------------------------------------------------------------------------
// Streaming query — yields StreamEvent objects from the SSE endpoint
// ---------------------------------------------------------------------------

export async function* streamQuery(
  payload: QueryPayload,
  signal?: AbortSignal
): AsyncGenerator<StreamEvent, void, unknown> {
  const response = await fetch(`${BACKEND_URL}/query/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => "unknown error");
    throw new Error(`Backend error ${response.status}: ${text}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE messages are separated by double newlines
      const messages = buffer.split("\n\n");
      buffer = messages.pop() ?? "";

      for (const message of messages) {
        if (!message.trim()) continue;

        // Extract the data line(s)
        const dataLine = message
          .split("\n")
          .find((l) => l.startsWith("data: "));
        if (!dataLine) continue;

        const jsonStr = dataLine.slice(6); // remove "data: "
        try {
          const event = JSON.parse(jsonStr) as StreamEvent;
          yield event;
          if (event.type === "done" || event.type === "error") return;
        } catch {
          // Malformed event — log and continue
          console.warn("Failed to parse SSE event:", jsonStr);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// Batch query (non-streaming) — for the health check page
// ---------------------------------------------------------------------------

export async function batchQuery(payload: QueryPayload): Promise<Answer | null> {
  const response = await fetch(`${BACKEND_URL}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) return null;
  const data = await response.json();
  return data.answer ?? null;
}

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

export async function fetchHealth(): Promise<{
  status?: string;
  version?: string;
  environment?: string;
  error?: string;
}> {
  try {
    const res = await fetch(`${BACKEND_URL}/health`, { cache: "no-store" });
    if (!res.ok) return { error: `backend ${res.status}` };
    return await res.json();
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
}

export async function fetchTickers(): Promise<{
  tickers: string[];
  fiscal_years: number[];
}> {
  const res = await fetch(`${BACKEND_URL}/tickers`, { cache: "no-store" });
  if (!res.ok) return { tickers: [], fiscal_years: [] };
  return await res.json();
}

// ---------------------------------------------------------------------------
// Type helpers
// ---------------------------------------------------------------------------

export function asPlanData(event: StreamEvent): PlanEventData {
  return event.data as unknown as PlanEventData;
}
export function asToolCallData(event: StreamEvent): ToolCallEventData {
  return event.data as unknown as ToolCallEventData;
}
export function asToolResultData(event: StreamEvent): ToolResultEventData {
  return event.data as unknown as ToolResultEventData;
}
export function asReflectionData(event: StreamEvent): ReflectionEventData {
  return event.data as unknown as ReflectionEventData;
}
export function asSynthesisData(event: StreamEvent): SynthesisEventData {
  return event.data as unknown as SynthesisEventData;
}
export function asDoneData(event: StreamEvent): DoneEventData {
  return event.data as unknown as DoneEventData;
}
