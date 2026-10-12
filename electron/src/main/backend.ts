// Spawns and supervises the Python backend (`python main.py --backend-only`).
// Loopback only; inherits process env so OLLAMA_HOST/TRACE_MODEL pass through.
import { spawn, ChildProcess } from "child_process";
import * as path from "path";

export function repoRoot(fromDir: string): string {
  // dist/main -> electron -> repo root
  return path.resolve(fromDir, "..", "..", "..");
}

export class BackendManager {
  proc: ChildProcess | null = null;
  baseUrl: string;
  private stopping = false;

  constructor(public port = 3777) {
    this.baseUrl = `http://127.0.0.1:${port}/api/v1`;
  }

  async health(timeoutMs = 3000): Promise<boolean> {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), timeoutMs);
      const r = await fetch(`${this.baseUrl}/health`, { signal: ctrl.signal });
      clearTimeout(t);
      return r.ok;
    } catch {
      return false;
    }
  }

  spawn(fromDir: string): void {
    if (this.proc) return;
    const root = repoRoot(fromDir);
    const resources = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath;
    const packaged = resources ? path.join(resources, "backend", "trace-backend.exe") : "";
    const executable = packaged && require("fs").existsSync(packaged)
      ? packaged : (process.env.TRACE_PYTHON || "python");
    const args = executable === packaged
      ? ["--port", String(this.port)]
      : [path.join(root, "main.py"), "--backend-only", "--port", String(this.port)];
    this.proc = spawn(executable, args, {
      cwd: executable === packaged ? path.dirname(executable) : root,
      stdio: ["ignore", "ignore", "pipe"],
      windowsHide: true,
    });
    this.proc.stderr?.on("data", (d) => console.error(`[backend] ${String(d).trim()}`));
    this.proc.on("exit", (code) => {
      if (!this.stopping) console.error(`[backend] exited with code ${code}`);
      this.proc = null;
    });
    this.proc.on("error", (e) => {
      console.error(`[backend] spawn failed: ${e.message}`);
      this.proc = null;
    });
  }

  async ensure(fromDir: string, waitMs = 15000): Promise<boolean> {
    if (await this.health()) return true;
    this.spawn(fromDir);
    const deadline = Date.now() + waitMs;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 250));
      if (!this.proc) return false; // died immediately
      if (await this.health()) return true;
    }
    return false;
  }

  stop(): void {
    this.stopping = true;
    try {
      this.proc?.kill();
    } catch {
      /* ignore */
    }
    this.proc = null;
  }
}
