// Shared backend-contract types. Mirrors docs/API_CONTRACT.md.
// The renderer never talks to Ollama; only these loopback shapes.

export interface StartRequest {
  request_text: string;
  project_dir?: string;
  include_screenshot?: boolean;
  screenshot_mode?: "active" | "full";
  include_clipboard?: boolean;
  editor_selection?: string;
  terminal_output?: string;
  extra_context?: string;
  auto_fix?: boolean;
  run_tests?: boolean;
  investigation_type?: "debug" | "explain";
}

export interface ProgressEvent {
  investigation_id: string;
  stage: string;
  message: string;
  progress: number;
  timestamp: number;
}

export interface Finding {
  title: string;
  file_path: string;
  start_line: number;
  end_line: number;
  code_excerpt: string;
  explanation: string;
  evidence: string;
  confidence: string;
  verified: boolean;
  git_context?: string;
}

export interface RecommendedFix {
  description: string;
  diff?: string | null;
  rationale?: string;
  diff_verified?: boolean;
}

export interface FixApplied {
  applied: boolean;
  applied_files: string[];
  backup_paths: string[];
  warnings: string[];
  dry_run: boolean;
}

export interface InvestigationReport {
  investigation_id: string;
  summary: string;
  root_cause: string;
  confidence: string;
  confidence_rationale: string;
  findings: Finding[];
  recommended_fix?: RecommendedFix | null;
  alternative_hypotheses: string[];
  needs_more_info: string[];
  evidence_summary: string;
  warnings: string[];
  created_at: number;
  fix_applied?: FixApplied | null;
}

export type InvestigationState = "running" | "done" | "error" | "cancelled";

export interface InvestigationStatus {
  investigation_id: string;
  status: InvestigationState;
  progress: number;
  stage: string;
  report?: InvestigationReport | null;
  error?: { code: string; message: string; hint?: string } | null;
}

// Labels shown ONLY for backend stages actually received. Never simulated.
export const STAGE_LABELS: Record<string, [string, string]> = {
  receive: ["Sending request", "Sending your debugging request"],
  screenshot: ["Capturing context", "Capturing screenshot and window context"],
  context: ["Capturing context", "Collecting local context"],
  project_root: ["Inspecting the project", "Identifying the project root"],
  error_extract: ["Analyzing the error", "Extracting error and stack trace"],
  read_files: ["Inspecting the project", "Reading relevant source files"],
  diagnostics: ["Inspecting the project", "Collecting compiler and linter diagnostics"],
  model_analysis: ["Analyzing the error", "Thinking through the evidence"],
  validate: ["Verifying file references", "Verifying paths and line numbers"],
  report: ["Preparing the explanation", "Preparing the explanation"],
};

export const TERMINAL_STAGES = ["done", "error", "cancelled"] as const;

export const TERMINAL_LABELS: Record<string, [string, string]> = {
  done: ["Done", "Investigation complete"],
  error: ["Error", "Investigation failed"],
  cancelled: ["Cancelled", "Investigation cancelled"],
};

export function stageLabel(stage: string): [string, string] {
  if (stage in STAGE_LABELS) return STAGE_LABELS[stage];
  if (stage in TERMINAL_LABELS) return TERMINAL_LABELS[stage];
  return ["Working", stage || "Working"];
}

export const KNOWN_STAGES = new Set([
  ...Object.keys(STAGE_LABELS),
  ...Object.keys(TERMINAL_LABELS),
]);
