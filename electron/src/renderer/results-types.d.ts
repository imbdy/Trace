// Renderer-local structural types for the results window (script, NOT a
// module: no imports/exports, emits nothing). Source of truth for the
// contract is src/shared/protocol.ts.
interface RendererFinding {
  title: string; file_path: string; start_line: number; end_line: number;
  code_excerpt: string; explanation: string; evidence: string;
  confidence: string; verified: boolean;
  git_context?: string;
}
interface RendererFix {
  description: string;
  diff?: string | null;
  rationale?: string;
  diff_verified?: boolean;
}
interface RendererFixApplied {
  applied: boolean; applied_files: string[]; backup_paths: string[];
  warnings: string[]; dry_run: boolean;
}
interface RendererReport {
  summary: string; root_cause: string; confidence: string;
  confidence_rationale: string; findings: RendererFinding[];
  recommended_fix?: RendererFix | null;
  alternative_hypotheses: string[]; needs_more_info: string[];
  evidence_summary: string; warnings: string[];
  fix_applied?: RendererFixApplied | null;
  // Provenance: when the verdict was issued (contract = seconds epoch).
  created_at?: number | string | null;
}
interface RendererStatus {
  investigation_id: string;
  status: "running" | "done" | "error" | "cancelled";
  progress: number; stage: string;
  // "debug" (default) or "explain": tailors the verdict view (no fix/apply
  // sections when there is no fix).
  investigation_type: string;
  report?: RendererReport | null;
  error?: { code: string; message: string; hint?: string } | null;
}
interface TraceWindowBridge {
  openFile: (path: string, line?: number) => Promise<Record<string, unknown>>;
  // Asks the main process to save the currently shown report as a Markdown
  // file (user chooses the destination); never writes without the dialog.
  saveReport: (status: unknown) => Promise<{ ok: boolean; path?: string; reason?: string; error?: string }>;
  applyFix: (id: string, dryRun?: boolean) => Promise<Record<string, unknown>>;
  followUp: (parentId: string, text: string) => Promise<{ ok: boolean; id?: string; reason?: string }>;
  history: (query?: string) => Promise<Record<string, unknown>[] | null>;
  showHistory: (id: string) => Promise<{ ok: boolean; reason?: string }>;
  onReport: (cb: (status: unknown) => void) => void;
}
interface Window { trace: TraceWindowBridge }
declare const window: Window;
