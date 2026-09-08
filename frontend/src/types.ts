/** API contracts mirrored from src/travel_agent_harness/api/schemas.py and service.py. */

export type TaskStatus =
  | "created"
  | "running"
  | "waiting_approval"
  | "completed"
  | "exhausted"
  | "failed";

export type ReportStatus = "not_requested" | "disabled" | "pending" | "running" | "completed" | "failed" | "skipped";

export interface ToolCatalogEntry {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  requires_approval: boolean;
  side_effect_free: boolean;
  input_guardrails: number;
  output_guardrails: number;
}

export interface RuntimeConfig {
  model: string;
  report_model: string | null;
  protocol: string;
  product_pipeline: string[];
  runtime: {
    engine: string;
    state_store: string;
    checkpoint: string;
    trace_transport: string;
    final_policy: string;
    tool_provider: string;
  };
  state_machine: string[];
  capabilities: string[];
  tools: ToolCatalogEntry[];
  limits: {
    max_steps: number;
    max_seconds: number;
    max_total_tokens: number;
    max_tool_calls: number;
    max_tool_output_chars: number;
  };
}

export interface PendingCall {
  call_id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface TaskMetrics {
  step: number;
  elapsed_seconds: number;
  total_tokens: number;
  tool_calls: number;
  successful_tool_calls: number;
  tool_errors: number;
  validation_errors: number;
}

export interface TaskLimits {
  max_steps: number;
  max_seconds: number;
  max_total_tokens: number;
  max_tool_calls: number;
}

export interface Guardrails {
  schema_validation: { enabled: boolean; passed: boolean; validation_errors: number };
  evidence_gate: { required: boolean; passed: boolean; successful_tools: number };
  repeat_call_limit: number;
  tool_output_limit_chars: number;
  trace_payloads: boolean;
}

export interface RouteStop {
  name: string;
  time: string;
  duration_minutes: number | null;
  cost_cny: number | null;
  category: "transport" | "food" | "stay" | "sight" | "activity" | "other";
  transport_to_next: string;
  note: string;
  address: string;
  image_url: string;
  coordinates: { lng: number; lat: number } | null;
}

export interface RouteDay {
  day: number;
  date: string;
  theme: string;
  stops: RouteStop[];
}

export interface RouteReport {
  title: string;
  subtitle: string;
  summary: string;
  map_kind: "geo" | "schematic";
  days: RouteDay[];
  budget: {
    total_cny: number | null;
    items: { label: string; amount_cny: number | null }[];
  };
  alerts: string[];
  evidence_notes: string[];
}

export interface TaskView {
  task_id: string;
  status: TaskStatus;
  objective: string;
  answer: string | null;
  report: RouteReport | null;
  report_status: ReportStatus;
  report_error: string | null;
  error: string | null;
  pending_call: PendingCall | null;
  model: string;
  report_model: string | null;
  checkpoint_seq: number;
  metrics: TaskMetrics;
  limits: TaskLimits;
  guardrails: Guardrails;
  created_at: number;
  updated_at: number;
}

export interface TraceEvent {
  id: number;
  created_at: number;
  step: number;
  kind: string;
  payload: Record<string, unknown>;
}

export interface CheckpointInfo {
  seq: number;
  created_at: number;
  status: TaskStatus;
  step: number;
}

export interface TripPlanPayload {
  origin: string;
  destination: string;
  start_date: string;
  days: number;
  budget_cny: number;
  travelers: number;
  pace: "relaxed" | "balanced" | "intensive";
  preferences: string[];
  notes: string;
}
