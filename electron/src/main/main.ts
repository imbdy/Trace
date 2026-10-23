// Trace desktop app: Neo's perch, Trace's backend.
//
// Window management is Neo's craft, kept intact: the native window hugs the
// visible cluster (face + unfolding leaf), perches top-center with a slight
// overhang, resizes only on renderer request within clamps — re-perching at
// every size so he never drifts off-centre — re-anchors on display changes,
// and lives in the tray. What changed: the Neoma watcher
// (core/watch/brief/vision) is gone; instead this spawns Trace's Python
// backend, runs investigations through it, and owns the results window.
// Renderer processes stay dumb; all backend I/O lives here in main.
import { app, BrowserWindow, dialog, ipcMain, screen, shell, Notification, Tray, Menu, nativeImage } from "electron";
import { autoUpdater } from "electron-updater";
import * as path from "path";
import * as fs from "fs";

// placement.js / settings.js are Neo's, kept verbatim as dependency-free JS
// (node --test covers them); required here at runtime after copy-assets.
const placement = require("./placement") as {
  anchor: (workArea: unknown, w: number, h: number, position: string) => { x: number; y: number };
  sanitisePosition: (raw: unknown) => string;
};
const settings = require("./settings") as {
  defaults: () => Record<string, unknown>;
  load: (dir: string | null) => Record<string, unknown>;
  mayNotify: (s: Record<string, unknown>, date: Date) => boolean;
};
const reportExport = require("./report-export") as {
  reportMarkdown: (status: unknown) => string;
  sanitizeFileBase: (id: unknown) => string;
};

import { BackendManager } from "./backend";
import { ApiError, TraceApi } from "./traceApi";
import { InvestigationStatus, KNOWN_STAGES, ProgressEvent } from "../shared/protocol";

// Ambient cluster: 336px leaf + shadow pad, face row ~170px tall. The window
// overlaps the top edge (see placement OVERHANG) so it hangs off it.
const INITIAL_W = 360 + 28;
const INITIAL_H = 170 + 28;
const MIN_W = 200, MAX_W = 760, MIN_H = 160, MAX_H = 520;

// Trace app identity + icon. Single source: <repo>/assets/icons (SVG master,
// PNG rasters, multi-size ICO). copy-assets.js mirrors them into dist/icons
// so packaged builds resolve without the source tree.
const APP_ID = "Trace.Trace";
try {
  app.setAppUserModelId(APP_ID);
} catch {
  /* ignore: non-Windows or early init */
}
try {
  app.setName("Trace");
} catch {
  /* ignore */
}
function traceIconFile(preferIco: boolean): string | undefined {
  const names = preferIco
    ? ["trace.ico", "trace.png", "trace-256.png"]
    : ["trace.png", "trace-32.png", "trace.ico"];
  const bases: string[] = [];
  try {
    bases.push(path.join(__dirname, "..", "icons")); // packaged (dist/icons)
    bases.push(path.join(__dirname, "..", "..", "..", "assets", "icons")); // dev
    const res = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath;
    if (res) bases.push(path.join(res, "assets", "icons"));
  } catch {
    /* ignore */
  }
  for (const base of bases) {
    for (const n of names) {
      try {
        const p = path.join(base, n);
        if (fs.existsSync(p)) return p;
      } catch {
        /* next */
      }
    }
  }
  return undefined;
}

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
const userDataDir = (() => {
  const i = argv.indexOf("--user-data-dir");
  return i >= 0 && argv[i + 1] ? argv[i + 1] : undefined;
})();
if (userDataDir) {
  try {
    app.setPath("userData", userDataDir);
  } catch {
    /* ignore */
  }
}

const backend = new BackendManager(backendPort);
const api = new TraceApi(backend.baseUrl);
let charWin: BrowserWindow | null = null;
let resultsWin: BrowserWindow | null = null;
let tray: Tray | null = null;
let run: { id: string; abort: AbortController } | null = null;
let lastStatus: InvestigationStatus | null = null;
let display: Electron.Display | null = null;

let cfg: Record<string, unknown> = settings.defaults();

function settingsDir(): string | null {
  try {
    return app && app.getPath ? path.join(app.getPath("userData"), "trace") : null;
  } catch {
    return null;
  }
}
function mayNudge(): boolean {
  try {
    return settings.mayNotify(cfg, new Date());
  } catch {
    return true;
  }
}

function homeDisplay(): Electron.Display {
  try {
    return screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
  } catch {
    return screen.getPrimaryDisplay();
  }
}
function perchPos(w: number, h: number): { x: number; y: number } {
  const d = display || screen.getPrimaryDisplay();
  return placement.anchor(d.workArea, w, h, placement.sanitisePosition(cfg.position));
}

function distFile(...parts: string[]): string {
  return path.join(__dirname, ...parts);
}

function createCharacter(): BrowserWindow {
  display = homeDisplay();
  const at = perchPos(INITIAL_W, INITIAL_H);
  const win = new BrowserWindow({
    x: at.x,
    y: at.y,
    width: INITIAL_W,
    height: INITIAL_H,
    icon: traceIconFile(process.platform === "win32"),
    title: "Trace",
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    closable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: true,
    show: false,
    hasShadow: false, // soft shadow is drawn inside the leaf
    backgroundColor: "#00000000",
    webPreferences: {
      preload: distFile("..", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.setAlwaysOnTop(true, "screen-saver");
  try {
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  } catch {
    /* ignore */
  }
  win.loadFile(distFile("..", "renderer", "neo", "index.html"));
  win.setIgnoreMouseEvents(false);
  win.once("ready-to-show", () => {
    try {
      win.setAlwaysOnTop(true, "screen-saver");
    } catch {
      /* ignore */
    }
    if (SMOKE) win.showInactive();
    else win.show();
  });
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
    icon: traceIconFile(process.platform === "win32"),
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: distFile("..", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.loadFile(distFile("..", "renderer", "results.html"));
  win.setMenu(null);
  return win;
}

// Broadcasts a final status to any EXISTING windows only — never creates or
// shows one. Used for the quiet fix-in-place path and as the fan-out half of
// showResults.
function sendReport(status: InvestigationStatus): void {
  try {
    charWin?.webContents.send("trace:report", status);
  } catch {
    /* ignore */
  }
  if (resultsWin && !resultsWin.isDestroyed()) {
    try {
      resultsWin.webContents.send("trace:report", status);
    } catch {
      /* ignore */
    }
  }
}

function showResults(status?: InvestigationStatus | null): boolean {
  const st = status ?? lastStatus;
  if (!st) return false;
  if (!resultsWin || resultsWin.isDestroyed()) {
    if (!app.isReady()) return false;
    resultsWin = createResults();
  }
  // Both windows hear the verdict: the results window renders it, the
  // character window performs it (panel finish + creature state).
  sendReport(st);
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
  return true;
}

function notifyDone(title: string, body: string): void {
  try {
    if (!mayNudge()) return;
    const note = new Notification({ title, body, silent: false });
    note.on("click", () => showResults());
    note.show();
  } catch (e) {
    console.error("Trace: notification failed: " + (e as Error).message);
  }
}

let updateReady = false;
function setupAutoUpdates(): void {
  if (!app.isPackaged || SMOKE) return;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.on("update-available", () => {
    notifyDone("Trace is updating", "A new version is downloading in the background.");
  });
  autoUpdater.on("update-downloaded", () => {
    updateReady = true;
    notifyDone("Trace is ready to restart", "The update will be installed the next time Trace starts.");
  });
  setTimeout(() => { void autoUpdater.checkForUpdates().catch(() => undefined); }, 7000);
}

function emitStage(ev: { investigation_id?: string; stage: string; message: string; progress: number }): void {
  // The latest visible stage doubles as an IN-FLIGHT status so "Open results"
  // during a run shows the running investigation (its current stage) instead
  // of stale last-run data. Only superseded by the real final status.
  if (ev.investigation_id && run && run.id === ev.investigation_id) {
    lastStatus = {
      investigation_id: ev.investigation_id,
      status: "running",
      progress: ev.progress,
      stage: ev.stage,
      report: null,
      error: null,
    };
  }
  try {
    charWin?.webContents.send("trace:stage", ev);
  } catch {
    /* ignore */
  }
}

function shapeRunError(e: unknown): { message: string; code: string } {
  const err = e as ApiError;
  const message = err instanceof ApiError
    ? `${err.message}${err.hint ? " " + err.hint : ""}`
    : String(e);
  return { message, code: (err as ApiError)?.code ?? "internal" };
}

function errorStatus(investigationId: string, shaped: { message: string; code: string }): InvestigationStatus {
  return {
    investigation_id: investigationId,
    status: "error",
    progress: 0,
    stage: "error",
    report: null,
    error: { code: shaped.code, message: shaped.message },
  };
}

// A transient loopback/backend blip while streaming must not pretend the run
// died, nor leave the backend burning model time unheard: re-open the event
// stream (the backend replays the full history), a bounded number of times;
// only give up and cancel the run after the reattach budget is exhausted.
const STREAM_REATTACH_MAX = 2;

// Streams one running investigation to the character window, then lands the
// final status (notifications + results window + both-report broadcast).
async function streamRun(id: string, abort: AbortController, autoFix: boolean): Promise<void> {
  let reattach = 0;
  try {
    while (true) {
      try {
        for await (const ev of api.events(id, abort.signal)) {
          reattach = 0; // a healthy chunk of events resets the retry budget
          const e = ev as ProgressEvent;
          emitStage({ investigation_id: id, stage: e.stage, message: e.message, progress: e.progress });
        }
        break;
      } catch (streamErr) {
        if ((streamErr as Error).name === "AbortError") throw streamErr;
        // Give a status attempt one shot: if the run is terminal already, the
        // landing path below can still finish the story honestly.
        let terminal = false;
        try {
          const probe = await api.getStatus(id);
          if (probe.status !== "running") {
            terminal = true;
          } else {
            emitStage({ investigation_id: id, stage: probe.stage ?? "receive", message: "Reconnecting…", progress: probe.progress });
          }
        } catch {
          /* status probe failed too — still try the bounded reattach */
        }
        if (terminal) break;
        if (reattach >= STREAM_REATTACH_MAX) {
          // No one can hear this run finish — stop it honestly, then surface.
          try { await api.cancel(id); } catch { /* ignore */ }
          throw streamErr;
        }
        reattach++;
        await new Promise((r) => setTimeout(r, 500 * reattach)); // gentle backoff
        if (abort.signal.aborted) {
          throw Object.assign(new Error("cancelled"), { name: "AbortError" });
        }
      }
    }
    if (abort.signal.aborted) {
      emitStage({ investigation_id: id, stage: "cancelled", message: "Cancelled.", progress: 0 });
      try {
        const st = await api.getStatus(id);
        lastStatus = st;
        showResults(st);
      } catch {
        /* ignore */
      }
      return;
    }
    const status = await api.getStatus(id);
    lastStatus = status;
    emitStage({
      investigation_id: id,
      stage: status.status === "done" ? "done" : status.status,
      message:
        status.status === "done"
          ? "Investigation complete."
          : status.error?.message ?? `Investigation ${status.status}.`,
      progress: status.status === "done" ? 1 : 0,
    });
    if (status.status === "done" && status.report) {
      const verified = (status.report.findings ?? []).filter((f) => f.verified).length;
      const fixed = status.report.fix_applied;
      if (autoFix && fixed && fixed.applied) {
        // "What's wrong, then go fix it": stay quiet, no results window —
        // the bug is already fixed in place (backups kept). Both windows
        // still hear the verdict: the panel settles and shows "Fixed"
        // instead of shimmering on the last stage forever.
        const files = (fixed.applied_files ?? []).join(", ") || "the files";
        notifyDone("Trace — fixed in place", `Fixed ${files}. Backups kept (*.trace-bak).`);
        sendReport(status);
        return;
      }
      notifyDone(
        "Trace — investigation complete",
        verified > 0
          ? `${verified} verified location${verified === 1 ? "" : "s"} — results are open.`
          : "No solid location — results are open.",
      );
    } else if (status.status === "error") {
      notifyDone("Trace — investigation hit a snag", status.error?.message ?? "See the results window.");
    }
    showResults(status);
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      emitStage({ investigation_id: id, stage: "cancelled", message: "Cancelled.", progress: 0 });
      return;
    }
    const shaped = shapeRunError(e);
    emitStage({ investigation_id: id, stage: "error", message: shaped.message.slice(0, 300), progress: 0 });
    const st = errorStatus(id, shaped);
    lastStatus = st;
    showResults(st);
  } finally {
    if (run && run.id === id) run = null;
  }
}

// Starts an investigation and hands streaming off to the background; resolves
// with the backend's investigation id as soon as it exists, so the renderer
// can identify (and filter stale events from) this run.
async function runInvestigation(text: string, opts: Record<string, unknown>, followUpOf?: string): Promise<string | null> {
  if (run) return null;
  const abort = new AbortController();
  // Reserve the single-run slot synchronously so two overlapping IPC submits
  // cannot both pass the guard above; the real id replaces "" on start.
  run = { id: "", abort };
  const autoFix = !followUpOf && (opts.autoFix === true || (opts as { auto_fix?: boolean }).auto_fix === true);
  const investigationType =
    (opts.investigation_type === "explain" ? "explain" : "debug");
  try {
    const id = followUpOf
      ? await api.followUp(followUpOf, text)
      : await api.start({
          request_text: text,
          project_dir: (opts.projectDir as string) || projectDirArg,
          include_screenshot: true,
          screenshot_mode: "active",
          include_clipboard: false,
          auto_fix: autoFix,
          investigation_type: investigationType,
        });
    run = { id, abort };
    emitStage({ investigation_id: id, stage: "receive", message: "Sending request…", progress: 0.02 });
    void streamRun(id, abort, autoFix);
    return id;
  } catch (e) {
    run = null;
    if ((e as Error).name === "AbortError") {
      emitStage({ stage: "cancelled", message: "Cancelled.", progress: 0 });
      return null;
    }
    const shaped = shapeRunError(e);
    emitStage({ stage: "error", message: shaped.message.slice(0, 300), progress: 0 });
    // run is already nulled on entry to this catch; the failed attempt has
    // no id yet, so the status honestly carries the empty id placeholder.
    const st = errorStatus("", shaped);
    lastStatus = st;
    showResults(st);
    return null;
  }
}

function buildTray(): void {
  const dot = (color: string): Electron.NativeImage => {
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">` +
      `<rect width="16" height="16" fill="none"/>` +
      `<circle cx="8" cy="8" r="5" fill="${color}"/></svg>`;
    return nativeImage.createFromDataURL("data:image/svg+xml;base64," + Buffer.from(svg).toString("base64"));
  };
  const refresh = (): void => {
    if (!tray) return;
    try {
      const menu = Menu.buildFromTemplate([
        { label: run ? "Trace is working…" : "Trace is ready", enabled: false },
        { type: "separator" },
        { label: "Show Trace", click: () => { try { charWin && charWin.show(); } catch { /* ignore */ } } },
        { label: "Open results", click: () => showResults() },
        { label: "Check for updates", enabled: app.isPackaged, click: () => { void autoUpdater.checkForUpdates().catch(() => undefined); } },
        { type: "separator" },
        { label: "Quit Trace", click: () => { try { app.quit(); } catch { /* ignore */ } } },
      ]);
      tray.setContextMenu(menu);
    } catch {
      /* ignore */
    }
  };
  try {
    // Prefer the real Trace tray icon; fall back to the legacy dot so a
    // missing asset never breaks startup (or the other AI's work).
    let trayImage: Electron.NativeImage | undefined;
    try {
      const file = traceIconFile(false);
      if (file) {
        const img = nativeImage.createFromPath(file);
        trayImage = img.isEmpty() ? undefined : img.resize({ width: 16, height: 16 });
      }
    } catch {
      trayImage = undefined;
    }
    tray = new Tray(trayImage && !trayImage.isEmpty() ? trayImage : dot("#5be08f"));
    tray.setToolTip("Trace — debugging companion");
    tray.on("click", () => { try { charWin && charWin.show(); } catch { /* ignore */ } });
    refresh();
    setInterval(() => refresh(), 30_000).unref?.();
  } catch {
    tray = null;
  }
}

function setupIpc(): void {
  // Renderer owns cluster geometry: it asks for w/h, main clamps + re-perches.
  ipcMain.on("trace:resize", (_e, w: number, h: number) => {
    if (!charWin || charWin.isDestroyed()) return;
    let ww = Math.round(Number(w)), hh = Math.round(Number(h));
    if (!Number.isFinite(ww) || !Number.isFinite(hh)) return;
    ww = Math.max(MIN_W, Math.min(MAX_W, ww));
    hh = Math.max(MIN_H, Math.min(MAX_H, hh));
    try {
      const [cw, ch] = charWin.getSize();
      if (SMOKE) console.error(`[smoke] resize asked ${ww}x${hh} (have ${cw}x${ch})`);
      if (cw === ww && ch === hh) return;
      // ONE atomic setBounds (a back-to-back setSize + setPosition silently
      // drops the size on some Windows drivers), and ALWAYS re-perch —
      // growing used to keep the top-left, so unfolding the leaf pushed the
      // whole box to the right of the middle. Every size now lands on the
      // same perch, so extending him never moves him off-centre.
      const at = perchPos(ww, hh);
      charWin.setBounds({ x: at.x, y: at.y, width: ww, height: hh });
    } catch {
      /* ignore */
    }
  });

  ipcMain.on("trace:hide", () => {
    try {
      charWin && charWin.hide();
    } catch {
      /* ignore */
    }
  });

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
    const id = await runInvestigation(text.trim().slice(0, 8000), opts ?? {});
    return id ? { ok: true as const, id } : { ok: true as const };
  });

  ipcMain.handle("trace:follow-up", async (_e, parentId: string, text: string) => {
    if (run) return { ok: false as const, reason: "busy" };
    if (typeof parentId !== "string" || !parentId.trim()) return { ok: false as const, reason: "missing_parent" };
    if (typeof text !== "string" || !text.trim()) return { ok: false as const, reason: "empty" };
    if (!(await backend.health())) {
      const ok = await backend.ensure(__dirname);
      if (!ok) return { ok: false as const, reason: "backend_down" };
    }
    const followId = await runInvestigation(text.trim().slice(0, 8000), {}, parentId.trim());
    return followId ? { ok: true as const, id: followId } : { ok: true as const };
  });

  // Normal chat, no investigation machinery: one model round-trip. Chat and
  // investigations may interleave (the backend queues LLM calls), but the
  // renderer's driver serializes its own sends anyway.
  ipcMain.handle("trace:chat",
    async (_e, message: string, history?: { role: string; content: string }[]) => {
      if (typeof message !== "string" || !message.trim()) {
        return { ok: false as const, reason: "empty" };
      }
      if (!(await backend.health())) {
        const ok = await backend.ensure(__dirname);
        if (!ok) return { ok: false as const, reason: "backend_down" };
      }
      const hist = Array.isArray(history)
        ? history.slice(-20).map((h) => ({
            role: String(h && h.role === "assistant" ? "assistant" : "user"),
            content: String(h && h.content ? String(h.content).slice(0, 4000) : ""),
          })).filter((h) => h.content)
        : [];
      try {
        const reply = await api.chat(message.trim().slice(0, 8000), hist);
        return { ok: true as const, reply };
      } catch (e) {
        const err = e as { code?: string; message?: string };
        return { ok: false as const, reason: err.code || "chat_failed", error: err.message };
      }
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

  ipcMain.handle("trace:show-results", async () => ({ ok: showResults() }));

  ipcMain.handle("trace:current", async () => ({ id: run ? run.id : null }));

  ipcMain.handle("trace:history", async (_e, query: string) => {
    try { return await api.history(50, typeof query === "string" ? query : ""); }
    catch { return null; } // null = backend down (distinct from "no history")
  });

  ipcMain.handle("trace:show-history", async (_e, id: string) => {
    if (typeof id !== "string" || !id.trim()) return { ok: false as const, reason: "missing_id" };
    try {
      const st = await api.getStatus(id.trim());
      lastStatus = st;
      return { ok: showResults(st) };
    } catch { return { ok: false as const, reason: "not_found" }; }
  });

  ipcMain.handle("trace:open-file", async (_e, filePath: string, line?: number) => {
    try {
      return await api.openFile(String(filePath), typeof line === "number" ? line : undefined);
    } catch (e) {
      return { ok: false, error: (e as Error).message };
    }
  });

  ipcMain.handle("trace:apply-fix", async (_e, id: string, dryRun?: boolean) => {
    if (typeof id !== "string" || !id.trim()) return { ok: false, error: "missing id" };
    try {
      return await api.applyFix(id.trim(), !!dryRun);
    } catch (e) {
      const err = e as ApiError;
      return { ok: false, error: err.message, code: (err as ApiError).code };
    }
  });

  ipcMain.handle("trace:save-report", async (e, payload: unknown) => {
    try {
      const md = reportExport.reportMarkdown(payload);
      const st = (payload && typeof payload === "object" ? payload : {}) as { investigation_id?: unknown };
      const base = reportExport.sanitizeFileBase(st.investigation_id);
      const win = BrowserWindow.fromWebContents(e.sender);
      const dialogOpts = {
        title: "Save Trace report",
        defaultPath: `${base}.md`,
        filters: [{ name: "Markdown", extensions: ["md"] }],
      };
      const res = win
        ? await dialog.showSaveDialog(win, dialogOpts)
        : await dialog.showSaveDialog(dialogOpts);
      if (res.canceled || !res.filePath) return { ok: false as const, reason: "canceled" };
      await fs.promises.writeFile(res.filePath, md, "utf8");
      return { ok: true as const, path: res.filePath };
    } catch (err) {
      return { ok: false as const, error: (err as Error).message };
    }
  });
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
  // Neo-correct: the window hugs the leaf (336px + pads) and hangs 10px
  // over the top edge — exact width follows content, not the initial size.
  A(b.y === -10 && b.width >= 336 && b.width <= 440, "character perched top-center with overhang", JSON.stringify(b));
  A(b.x >= 0, "character inside workarea", `x=${b.x}`);

  const dom = await charWin.webContents.executeJavaScript(`(() => ({
    hearth: !!document.querySelector('canvas#hearth'),
    chevronGone: !document.querySelector('#tab'),
    composer: !!document.querySelector('#ask-text'),
    fixToggle: !!document.querySelector('#ask-fix'),
    steps: Array.from(document.querySelectorAll('#stitch .st')).map((s) => s.getAttribute('data-s')),
    dots: !!document.querySelector('.three-dots,.typing-indicator'),
    bridge: typeof window.trace?.submit === 'function',
    fixBridge: typeof window.trace?.applyFix === 'function',
    title: document.querySelector('#leaf-title')?.textContent,
    neoma: (document.body.innerText || '').toLowerCase().includes('neoma'),
  }))()`);
  A(dom.hearth, "Neo face canvas present");
  A(dom.chevronGone, "chevron button removed");
  A(dom.composer, "composer present");
  A(dom.fixToggle, "fix-it-for-me toggle present");
  A(dom.fixBridge, "apply-fix bridge exposed");
  A(JSON.stringify(dom.steps) === JSON.stringify(["capture", "inspect", "analyze", "verify", "report"]),
    "stitch tracks investigation stages", JSON.stringify(dom.steps));
  A(!dom.dots, "no three-dot indicator in DOM");
  A(dom.bridge, "preload bridge exposed, renderer isolated");
  A(dom.title === "Trace", "rebranded to Trace", String(dom.title));
  A(!dom.neoma, "no Neoma references in visible UI");

  // Horizontal expansion: click anywhere on the collapsed shell.
  const hb0 = charWin.getBounds();
  await charWin.webContents.executeJavaScript(`document.querySelector('#leaf').click()`);
  await new Promise((r) => setTimeout(r, 700));
  const hb1 = charWin.getBounds();
  const openState = await charWin.webContents.executeJavaScript(`(() => ({
    open: document.querySelector('#leaf').classList.contains('open'),
    focused: document.activeElement ? document.activeElement.id : '',
  }))()`);
  A(openState.open === true, "shell click expands the chat");
  A(hb1.width === 632 && hb1.x + hb1.width / 2 === hb0.x + hb0.width / 2,
    "expansion re-perches: he stays in the middle", JSON.stringify(hb1));
  // The composer now carries a one-line "fix it for me" toggle, so the open
  // leaf is slightly taller than the collapsed sill — bounded growth only.
  A(hb1.height >= hb0.height && hb1.height - hb0.height <= 60,
    "expansion grows only by the composer rows", `${hb0.height} -> ${hb1.height}`);
  A(openState.focused === "ask-text", "composer focused, ready to type");
  await charWin.webContents.executeJavaScript(`document.querySelector('#leaf-fold').click()`);
  await new Promise((r) => setTimeout(r, 600));
  const foldState = await charWin.webContents.executeJavaScript(`(() => ({
    open: document.querySelector('#leaf').classList.contains('open'),
  }))()`);
  const hb2 = charWin.getBounds();
  A(foldState.open === false, "fold handler ran (leaf class removed)", JSON.stringify(foldState));
  A(hb2.width === 408 && hb2.x === hb0.x, "fold collapses back, still in the middle", JSON.stringify(hb2));

  const emptyRes = await charWin.webContents.executeJavaScript(
    `window.trace.submit('   ').then((r) => JSON.stringify(r))`);
  A(emptyRes.includes('"ok":false'), "empty submit rejected", emptyRes);
  const cancelIdle = await charWin.webContents.executeJavaScript(
    `window.trace.cancel().then((r) => JSON.stringify(r))`);
  A(cancelIdle.includes('"ok":false'), "cancel with no run is a safe no-op", cancelIdle);

  // Drive the FULL integrated path: renderer bridge submit -> main runs the
  // real backend investigation -> SSE stages flow back to the renderer.
  const text = "Why does main.py fail on startup? Find the exact lines.";
  const subRes = await charWin.webContents.executeJavaScript(
    `window.trace.submit(${JSON.stringify(text)}).then((r) => JSON.stringify(r))`);
  A(subRes.includes('"ok":true'), "submit starts an investigation", subRes);
  let id = "";
  for (let i = 0; i < 40 && !id; i++) {
    try {
      const cur = await charWin.webContents.executeJavaScript(
        `window.trace.current().then((r) => r && r.id ? r.id : '')`);
      if (cur) id = cur;
      else await new Promise((r) => setTimeout(r, 250));
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  A(!!id, "investigation starts (real backend)", id);
  if (!id) return 1;
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

  // Creature + panel must reflect the real outcome (poll: success settles in ~6s).
  let face = "", stitch = "", verdict = "";
  for (let i = 0; i < 40; i++) {
    const snap = await charWin.webContents.executeJavaScript(`(() => ({
      face: window.TraceFace ? window.TraceFace.state : 'missing',
      reportTick: !!document.querySelector('#stitch [data-s="report"].tick'),
      verdict: (document.querySelector('#verdict-line')?.textContent || '').length,
    }))()`);
    face = snap.face; stitch = String(snap.reportTick); verdict = String(snap.verdict);
    if ((face === "success" || face === "idle") && snap.reportTick && snap.verdict > 10) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  // NOTE: main auto-opened results on done; face may have settled back to idle
  // after its 6s performance — accept either, but the stitch + verdict persist.
  A(face === "success" || face === "idle", "creature shows success then settles", `face=${face}`);
  A(stitch === "true", "stitch lit report from real stages");
  A(Number(verdict) > 10, "verdict block holds the real summary");

  if (!resultsWin || resultsWin.isDestroyed()) resultsWin = createResults();
  showResults(status);
  await new Promise((r) => setTimeout(r, 1200));
  A(resultsWin.isVisible(), "results window opens on completion");
  const rdom = await resultsWin.webContents.executeJavaScript(`(() => ({
    sections: document.querySelectorAll('[data-section]').length,
    finding: document.querySelectorAll('[data-finding]').length,
  }))()`);
  A(rdom.sections >= 4, "results window has all evidence sections", JSON.stringify(rdom));

  console.log(ok ? "--- SMOKE PASSED ---" : "--- SMOKE FAILED ---");
  return ok ? 0 : 1;
}

async function startup(): Promise<void> {
  cfg = settings.load(settingsDir());
  setupIpc();
  const okBackend = await backend.ensure(__dirname);
  charWin = createCharacter();
  resultsWin = createResults();
  buildTray();
  setupAutoUpdates();
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
        if (charWin && !charWin.isDestroyed()) charWin.show();
      } catch {
        /* ignore */
      }
    });
    app.whenReady().then(() => startup()).catch((e) => console.error(e));
  }
}

// Tray app: stay alive while windows are hidden (closable:false + tray Quit).
// The event object is untyped in current @types, hence the rest-arg read.
app.on("window-all-closed", (...args: unknown[]) => {
  const e = args[0] as { preventDefault?: () => void } | undefined;
  if (process.platform !== "darwin") e?.preventDefault?.();
});

app.on("before-quit", () => {
  try {
    if (run) run.abort.abort();
    if (updateReady) autoUpdater.quitAndInstall();
  } catch {
    /* ignore */
  }
  backend.stop();
});

function reanchor(): void {
  if (!charWin || charWin.isDestroyed()) return;
  try {
    const [w, h] = charWin.getSize();
    const at = perchPos(w, h);
    charWin.setPosition(at.x, at.y);
  } catch {
    /* ignore */
  }
}

try {
  if (screen && screen.on) {
    screen.on("display-added", reanchor);
    screen.on("display-removed", reanchor);
    screen.on("display-metrics-changed", reanchor);
  }
} catch {
  /* single static display */
}
