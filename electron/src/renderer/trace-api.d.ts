// Global renderer API surface (script, NOT a module: no imports/exports).
// The renderer is intentionally import-free so the TypeScript compiler emits
// plain <script>-runnable JS (no CommonJS boilerplate) over file:// with
// nodeIntegration disabled. Structural type copies only — source of truth
// for the contract is src/shared/protocol.ts.
interface TraceStageEvent { stage: string; message: string; progress: number }
interface TraceCursor { x: number; y: number }
interface TraceBridge {
  submit: (text: string, opts?: Record<string, unknown>) => Promise<{ ok: boolean; id?: string; reason?: string }>;
  cancel: () => Promise<{ ok: boolean }>;
  openFile: (path: string, line?: number) => Promise<Record<string, unknown>>;
  resize: (w: number, h: number) => void;
  onStage: (cb: (ev: TraceStageEvent) => void) => void;
  onReport: (cb: (status: RendererStatus) => void) => void;
  onBackend: (cb: (info: { ok: boolean; message: string }) => void) => void;
  onCursor: (cb: (pt: TraceCursor) => void) => void;
}
interface RendererFinding {
  title: string; file_path: string; start_line: number; end_line: number;
  code_excerpt: string; explanation: string; evidence: string;
  confidence: string; verified: boolean;
}
interface RendererFix { description: string; diff?: string | null; rationale?: string }
interface RendererReport {
  summary: string; root_cause: string; confidence: string;
  confidence_rationale: string; findings: RendererFinding[];
  recommended_fix?: RendererFix | null;
  alternative_hypotheses: string[]; needs_more_info: string[];
  evidence_summary: string; warnings: string[];
}
interface RendererStatus {
  investigation_id: string;
  status: "running" | "done" | "error" | "cancelled";
  progress: number; stage: string;
  report?: RendererReport | null;
  error?: { code: string; message: string; hint?: string } | null;
}
interface Window { traceAPI: TraceBridge }
declare const window: Window;
