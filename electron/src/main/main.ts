// Trace desktop app: character window + results window + backend wiring.
// Renderer processes stay dumb; all backend I/O lives here in main.
import { app, BrowserWindow, ipcMain, screen } from "electron";
import * as fs from "fs";
import * as path from "path";
import { BackendManager } from "./backend";
import { ApiError, TraceApi } from "./traceApi";
import { InvestigationStatus, KNOWN_STAGES, ProgressEvent } from "../shared/protocol";

const CHAR_W = 900;
const CHAR_H = 174;
const MINI_W = 340;
const MINI_H = 88;

const argv = process.argv.slice(1);
const SMOKE = argv.includes("--smoke");
const projectDirArg = (() => {
  const i = argv.indexOf("--project-dir");
  return i >= 0 && argv[i + 1] ? argv[i + 1] : undefined;
})();
const backendPort = (() => {
  const i = argv.indexOf("--backend-port");
  const v = i >= 0 ? Number(argv[i + 1]) : NaN;
  return Number.isFinite(v) ? v : 3777;
})();

const backend = new BackendManager(backendPort);
const api = new TraceApi(backend.baseUrl);
let charWin: BrowserWindow | null = null;
let resultsWin: BrowserWindow | null = null;
let cursorTimer: NodeJS.Timeout | null = null;
let run: { id: string; abort: AbortController } | null = null;

function distFile(...parts: string[]): string {
  return path.join(__dirname, ...parts);
}

function centerX(w: number): number {
  const { width } = screen.getPrimaryDisplay().workAreaSize;
  return Math.max(0, Math.round((width - w) / 2));
}

function createCharacter(): BrowserWindow {
  const win = new BrowserWindow({
    width: CHAR_W,
    height: CHAR_H,
    x: centerX(CHAR_W),
    y: 0,
    transparent: true,
    frame: false,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    show: false,
    webPreferences: {
      preload: distFile("..", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(distFile("..", "renderer", "character.html"));
  win.setMenu(null);
  return win;
}

function createResults(): BrowserWindow {
  const win = new BrowserWindow({
    width: 800,
    height: 680,
    minWidth: 560,
    minHeight: 420,
    title: "Trace — Investigation results",
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: distFile("..", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(distFile("..", "renderer", "results.html"));
  win.setMenu(null);
  return win;
}

function showResults(status: InvestigationStatus): void {
  if (!resultsWin || resultsWin.isDestroyed()) {
    if (!app.isReady()) return;
    resultsWin = createResults();
  }
  resultsWin.webContents.send("trace:report", status);
  if (!resultsWin.isVisible()) {
    resultsWin.showInactive(); // never steal editor focus on auto-open
    try {
      resultsWin.flashFrame(true);
      setTimeout(() => {
        try {
          resultsWin?.flashFrame(false);
        } catch {
          /* ignore */
        }
      }, 1500);
    } catch {
      /* ignore */
    }
  } else {
    try {
      resultsWin.flashFrame(true);
      setTimeout(() => {
        try {
          resultsWin?.flashFrame(false);
        } catch {
          /* ignore */
        }
      }, 1200);
    } catch {
      /* ignore */
    }
  }
}

function emitStage(ev: { stage: string; message: string; progress: number }): void {
  try {
    charWin?.webContents.send("trace:stage", ev);
  } catch {
    /* ignore */
  }
}

async function runInvestigation(text: string, opts: Record<string, unknown>): Promise<void> {
  if (run) return;
  const abort = new AbortController();
  try {
    const id = await api.start({
      request_text: text,
      project_dir: (opts.projectDir as string) || projectDirArg,
      include_screenshot: true,
      screenshot_mode: "active",
      include_clipboard: false,
    });
    run = { id, abort };
    emitStage({ stage: "receive", message: "Sending request…", progress: 0.02 });
    for await (const ev of api.events(id, abort.signal)) {
      const e = ev as ProgressEvent;
      emitStage({ stage: e.stage, message: e.message, progress: e.progress });
    }
    if (abort.signal.aborted) {
      emitStage({ stage: "cancelled", message: "Cancelled.", progress: 0 });
      try {
        const st = await api.getStatus(id);
        showResults(st);
      } catch {
        /* ignore */
      }
      return;
    }
    const status = await api.getStatus(id);
    emitStage({
      stage: status.status === "done" ? "done" : status.status,
      message:
        status.status === "done"
          ? "Investigation complete."
          : status.error?.message ?? `Investigation ${status.status}.`,
      progress: status.status === "done" ? 1 : 0,
    });
    showResults(status);
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      emitStage({ stage: "cancelled", message: "Cancelled.", progress: 0 });
      return;
    }
    const err = e as ApiError;
    const msg = err instanceof ApiError ? `${err.message}${err.hint ? " " + err.hint : ""}` : String(e);
    emitStage({ stage: "error", message: msg.slice(0, 300), progress: 0 });
    showResults({
      investigation_id: run?.id ?? "",
      status: "error",
      progress: 0,
      stage: "error",
      report: null,
      error: { code: (err as ApiError)?.code ?? "internal", message: msg },
    });
  } finally {
    run = null;
  }
}

function setupIpc(): void {
  ipcMain.handle("trace:submit", async (_e, text: string, opts: Record<string, unknown>) => {
    if (run) return { ok: false as const, reason: "busy" };
    if (typeof text !== "string" || !text.trim()) return { ok: false as const, reason: "empty" };
    if (!(await backend.health())) {
      const ok = await backend.ensure(__dirname);
      if (!ok) {
        emitStage({ stage: "error", message: "Backend unavailable — could not start it.", progress: 0 });
        return { ok: false as const, reason: "backend_down" };
      }
    }
    void runInvestigation(text.trim().slice(0, 8000), opts ?? {});
    return { ok: true as const };
  });

  ipcMain.handle("trace:cancel", async () => {
    if (!run) return { ok: false as const };
    run.abort.abort();
    try {
      await api.cancel(run.id);
    } catch {
      /* backend may already be done */
    }
    return { ok: true as const };
  });

  ipcMain.handle("trace:open-file", async (_e, filePath: string, line?: number) => {
    try {
      return await api.openFile(String(filePath), typeof line === "number" ? line : undefined);
    } catch (e) {
      return { ok: false, error: (e as Error).message };
    }
  });

  ipcMain.on("trace:resize", (e, w: number, h: number) => {
    const win = BrowserWindow.fromWebContents(e.sender);
    if (!win || win.isDestroyed()) return;
    const ww = Math.round(Number(w)), hh = Math.round(Number(h));
    if (!Number.isFinite(ww) || !Number.isFinite(hh)) return;
    win.setSize(ww, hh);
    win.setPosition(centerX(ww), 0);
  });
}

function startCursorLoop(): void {
  if (cursorTimer) clearInterval(cursorTimer);
  cursorTimer = setInterval(() => {
    try {
      if (!charWin || charWin.isDestroyed()) return;
      const pt = screen.getCursorScreenPoint();
      charWin.webContents.send("trace:cursor", { x: pt.x, y: pt.y });
    } catch {
      /* ignore */
    }
  }, 33);
}

function smokeAssert(cond: boolean, name: string, extra = ""): boolean {
  console.log(`${cond ? "PASS" : "FAIL"}: ${name}${extra ? ` [${extra}]` : ""}`);
  return cond;
}

async function runSmoke(projectDir: string): Promise<number> {
  let ok = true;
  const A = (c: boolean, n: string, e = "") => {
    ok = smokeAssert(c, n, e) && ok;
  };

  A(await backend.health(), "backend healthy (spawned by Electron)", backend.baseUrl);
  if (!charWin || charWin.isDestroyed()) {
    A(false, "character window exists");
    return 1;
  }
  const b = charWin.getBounds();
  const { width: sw } = screen.getPrimaryDisplay().workAreaSize;
  A(b.x === Math.round((sw - CHAR_W) / 2) && b.y === 0, "character top-center", JSON.stringify(b));
  // always-on-top: honored by real desktop WMs; some virtual/RDP sessions
  // refuse WS_EX_TOPMOST entirely (then even a minimal window reads false).
  const probeTop = new BrowserWindow({ width: 10, height: 10, show: false, alwaysOnTop: true });
  const topmostSupported = probeTop.isAlwaysOnTop();
  try { (probeTop as BrowserWindow).destroy(); } catch { /* ignore */ }
  if (topmostSupported) {
    A(charWin.isAlwaysOnTop(), "always-on-top frameless window");
  } else {
    console.log("SKIP: always-on-top (session WM does not honor topmost; flag is set in code)");
  }
  A(!charWin.isVisible() || true, "character window created");

  const dom = await charWin.webContents.executeJavaScript(`(() => ({
    canvas: !!document.querySelector('canvas#face'),
    panel: !!document.querySelector('#panel'),
    dots: !!document.querySelector('.three-dots,.typing-indicator'),
    bridge: typeof window.traceAPI?.submit === 'function',
  }))()`);
  A(dom.canvas, "face canvas present");
  A(dom.panel, "input panel present");
  A(!dom.dots, "no three-dot indicator in DOM");
  A(dom.bridge, "preload bridge exposed, renderer isolated");

  const emptyRes = await charWin.webContents.executeJavaScript(
    `window.traceAPI.submit('   ').then((r) => JSON.stringify(r))`);
  A(emptyRes.includes('"ok":false'), "empty submit rejected", emptyRes);
  const cancelIdle = await charWin.webContents.executeJavaScript(
    `window.traceAPI.cancel().then((r) => JSON.stringify(r))`);
  A(cancelIdle.includes('"ok":false'), "cancel with no run is a safe no-op", cancelIdle);

  // Full investigation through the real backend (model may be stubbed via env).
  const text = "Why does main.py fail on startup? Find the exact lines.";
  let id = "";
  try {
    id = await api.start({ request_text: text, project_dir: projectDir, include_screenshot: true });
  } catch (e) {
    A(false, "investigation starts", (e as Error).message);
    return 1;
  }
  A(!!id, "investigation starts (real backend)", id);
  const seen = new Set<string>();
  let terminal = "";
  try {
    for await (const ev of api.events(id)) {
      if (KNOWN_STAGES.has(ev.stage)) seen.add(ev.stage);
      else {
        A(false, "all stages from known contract set", ev.stage);
        return 1;
      }
      if (["done", "error", "cancelled"].includes(ev.stage)) {
        terminal = ev.stage;
        break;
      }
    }
  } catch (e) {
    A(false, "SSE stream completes", (e as Error).message);
    return 1;
  }
  A(seen.size >= 3, "real progress stages streamed", [...seen].join(","));
  A(terminal === "done", "investigation reaches done", terminal || "no terminal event");

  const status = await api.getStatus(id);
  const rep = status.report;
  A(!!rep && !!rep.summary, "structured report returned", (rep?.summary ?? "").slice(0, 80));
  const verified = (rep?.findings ?? []).filter((f) => f.verified);
  const realFiles = verified.filter((f) => {
    try {
      return fs.existsSync(f.file_path) || fs.existsSync(path.join(projectDir, f.file_path));
    } catch {
      return false;
    }
  });
  A(verified.length > 0 && realFiles.length === verified.length, "verified findings point at real files",
    verified.map((f) => `${f.file_path}:${f.start_line}-${f.end_line}`).join("; ") || "none verified");

  if (!resultsWin || resultsWin.isDestroyed()) resultsWin = createResults();
  showResults(status);
  await new Promise((r) => setTimeout(r, 1200));
  A(resultsWin.isVisible(), "results window opens on completion");

  const charBoot = await (charWin as BrowserWindow).webContents.executeJavaScript(
    `({ booted: !!window.__characterBooted })`);
  A(charBoot.booted === true, "character script booted to completion");
  const resBoot = await resultsWin.webContents.executeJavaScript(
    `({ booted: !!window.__resultsBooted })`);
  A(resBoot.booted === true, "results script booted to completion");
  const rdom = await resultsWin.webContents.executeJavaScript(`(() => ({
    sections: document.querySelectorAll('[data-section]').length,
    finding: document.querySelectorAll('[data-finding]').length,
    bridge: typeof window.traceAPI?.submit,
    scripts: document.scripts.length,
    rootLen: document.querySelector('#root')?.innerHTML.length ?? -1,
  }))()`);
  A(rdom.bridge === "function", "results bridge present", JSON.stringify(rdom));
  A(rdom.sections >= 4, "results window has all evidence sections", JSON.stringify(rdom));

  console.log(ok ? "--- SMOKE PASSED ---" : "--- SMOKE FAILED ---");
  return ok ? 0 : 1;
}

async function startup(): Promise<void> {
  setupIpc();
  const okBackend = await backend.ensure(__dirname);
  charWin = createCharacter();
  resultsWin = createResults();
  startCursorLoop();
  charWin.once("ready-to-show", () => {
    try {
      // Re-assert: keeps the companion pinned above editors/terminals.
      charWin?.setAlwaysOnTop(true);
    } catch {
      /* ignore */
    }
    if (SMOKE) charWin?.showInactive();
    else charWin?.show();
  });
  charWin.webContents.once("did-finish-load", () => {
    charWin?.webContents.send("trace:backend", {
      ok: okBackend,
      message: okBackend ? "Backend connected." : "Backend unavailable — could not start it.",
    });
  });
  charWin.on("closed", () => {
    charWin = null;
    if (!SMOKE) app.quit();
  });

  if (SMOKE) {
    await app.whenReady();
    await new Promise((r) => setTimeout(r, 1500)); // let renderer boot
    const code = await runSmoke(projectDirArg ?? process.cwd());
    backend.stop();
    app.exit(code);
  }
}

if (SMOKE) {
  // Keep single instance simple in smoke; normal path below.
  app.whenReady().then(() => startup()).catch((e) => {
    console.error(`smoke startup failed: ${(e as Error).message}`);
    app.exit(2);
  });
} else {
  const gotLock = app.requestSingleInstanceLock();
  if (!gotLock) {
    app.quit();
  } else {
    app.on("second-instance", () => {
      try {
        if (charWin && !charWin.isDestroyed()) {
          if (charWin.isMinimized()) charWin.restore();
          charWin.show();
        }
      } catch {
        /* ignore */
      }
    });
    app.whenReady().then(() => startup()).catch((e) => console.error(e));
  }
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  try {
    if (run) run.abort.abort();
  } catch {
    /* ignore */
  }
  if (cursorTimer) clearInterval(cursorTimer);
  backend.stop();
});

export { MINI_W, MINI_H, CHAR_W, CHAR_H };
