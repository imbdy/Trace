// Renderer-local structural types for the results window (script, NOT a
// module: no imports/exports, emits nothing). Source of truth for the
// contract is src/shared/protocol.ts.
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
interface TraceWindowBridge {
  openFile: (path: string, line?: number) => Promise<Record<string, unknown>>;
  onReport: (cb: (status: unknown) => void) => void;
}
interface Window { trace: TraceWindowBridge }
declare const window: Window;
