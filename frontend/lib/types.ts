/**
 * TypeScript types mirroring the backend Pydantic schemas.
 * Keep in sync with backend/agent/schemas.py and backend/api/streaming.py.
 */

// ---------------------------------------------------------------------------
// Stream events (from /query/stream SSE)
// ---------------------------------------------------------------------------

export type EventType =
  | "plan"
  | "tool_call"
  | "tool_result"
  | "reflection"
  | "synthesis"
  | "done"
  | "error";

export interface StreamEvent {
  type: EventType;
  data: Record<string, unknown>;
  trace_id: string;
  iteration: number;
  timestamp: string;
}

// Typed data payloads per event type
export interface PlanEventData {
  query: string;
  sub_tasks: Array<{ description: string; tool: string }>;
  rationale?: string;
  is_refined: boolean;
}

export interface ToolCallEventData {
  index: number;
  tool: string;
  description: string;
  inputs_preview: Record<string, unknown>;
}

export interface ToolResultEventData {
  index: number;
  tool: string;
  latency_ms: number;
  summary?: string;
  error?: string;
  success: boolean;
}

export interface ReflectionEventData {
  phase: "pre_synthesis" | "post_synthesis";
  passed: boolean;
  failures: string[];
  will_refine: boolean;
}

export interface SynthesisEventData {
  markdown: string;
  n_claims: number;
  n_citations: number;
  used_tools: string[];
  iterations: number;
}

export interface DoneEventData {
  answer: Answer;
  iterations: number;
  trace_id: string;
}

// ---------------------------------------------------------------------------
// Agent schemas
// ---------------------------------------------------------------------------

export interface Citation {
  filing_id: string;
  accession_number: string;
  ticker: string;
  form: "10-K" | "10-Q" | "8-K";
  fiscal_year: number;
  section: string;
  item_label?: string;
  char_offset_start: number;
  char_offset_end: number;
  quoted_text?: string;
}

export interface Claim {
  text: string;
  is_numeric: boolean;
  numeric_value?: number;
  numeric_unit?: string;
  citations: Citation[];
}

export interface Answer {
  query: string;
  markdown: string;
  claims: Claim[];
  trace_id: string;
  iterations: number;
  used_tools: string[];
  cost_usd?: number;
}

// ---------------------------------------------------------------------------
// UI state models
// ---------------------------------------------------------------------------

export type MessageRole = "user" | "assistant";

export interface ToolCallTrace {
  tool: string;
  description: string;
  latency_ms?: number;
  summary?: string;
  error?: string;
  success?: boolean;
}

export interface ReflectionTrace {
  phase: string;
  passed: boolean;
  failures: string[];
}

export interface MessageTrace {
  plan?: PlanEventData;
  tool_calls: ToolCallTrace[];
  reflections: ReflectionTrace[];
  iterations: number;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  answer?: Answer;
  trace?: MessageTrace;
  isStreaming?: boolean;
  error?: string;
}

export type StreamStatus =
  | "idle"
  | "planning"
  | "running_tools"
  | "reflecting"
  | "synthesizing"
  | "done"
  | "error";
