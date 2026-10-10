// Preload bridge: the ONLY API the renderer sees. No Node, no Electron,
// no backend URLs, no model access — just Trace actions and event streams.
import { contextBridge, ipcRenderer, IpcRendererEvent } from "electron";

export interface TraceBridge {
  submit: (text: string, opts?: Record<string, unknown>) => Promise<{ ok: boolean; id?: string; reason?: string }>;
  cancel: () => Promise<{ ok: boolean }>;
  openFile: (path: string, line?: number) => Promise<Record<string, unknown>>;
  resize: (w: number, h: number) => void;
  onStage: (cb: (ev: { stage: string; message: string; progress: number }) => void) => void;
  onReport: (cb: (status: unknown) => void) => void;
  onBackend: (cb: (info: { ok: boolean; message: string }) => void) => void;
  onCursor: (cb: (pt: { x: number; y: number }) => void) => void;
}

const api: TraceBridge = {
  submit: (text, opts) => ipcRenderer.invoke("trace:submit", text, opts ?? {}),
  cancel: () => ipcRenderer.invoke("trace:cancel"),
  openFile: (path, line) => ipcRenderer.invoke("trace:open-file", path, line),
  resize: (w, h) => ipcRenderer.send("trace:resize", w, h),
  onStage: (cb) => ipcRenderer.on("trace:stage", (_e: IpcRendererEvent, ev) => cb(ev)),
  onReport: (cb) => ipcRenderer.on("trace:report", (_e: IpcRendererEvent, st) => cb(st)),
  onBackend: (cb) => ipcRenderer.on("trace:backend", (_e: IpcRendererEvent, info) => cb(info)),
  onCursor: (cb) => ipcRenderer.on("trace:cursor", (_e: IpcRendererEvent, pt) => cb(pt)),
};

contextBridge.exposeInMainWorld("traceAPI", api);
