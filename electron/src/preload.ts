// Trace preload bridge: the ONLY API the renderer sees. Neo-shaped channels
// (resize/hide/settings) plus Trace investigation channels. No Node, no
// Electron, no backend URLs, no model access in the renderer.
import { contextBridge, ipcRenderer, IpcRendererEvent } from "electron";

export interface TraceBridge {
  resize: (w: number, h: number) => void;
  hide: () => void;
  submit: (text: string, opts?: Record<string, unknown>) => Promise<{ ok: boolean; id?: string; reason?: string }>;
  followUp: (parentId: string, text: string) => Promise<{ ok: boolean; reason?: string }>;
  cancel: () => Promise<{ ok: boolean }>;
  showResults: () => Promise<{ ok: boolean }>;
  current: () => Promise<{ id: string | null }>;
  history: (query?: string) => Promise<Record<string, unknown>[]>;
  showHistory: (id: string) => Promise<{ ok: boolean; reason?: string }>;
  openFile: (path: string, line?: number) => Promise<Record<string, unknown>>;
  onStage: (cb: (ev: { investigation_id?: string; stage: string; message: string; progress: number }) => void) => void;
  onReport: (cb: (status: unknown) => void) => void;
  onBackend: (cb: (info: { ok: boolean; message: string }) => void) => void;
  settings: () => Promise<Record<string, unknown>>;
  pause: (minutes: number) => void;
  resume: () => void;
}

const api: TraceBridge = {
  resize: (w, h) => ipcRenderer.send("trace:resize", w, h),
  hide: () => ipcRenderer.send("trace:hide"),
  submit: (text, opts) => ipcRenderer.invoke("trace:submit", text, opts ?? {}),
  followUp: (parentId, text) => ipcRenderer.invoke("trace:follow-up", parentId, text),
  cancel: () => ipcRenderer.invoke("trace:cancel"),
  showResults: () => ipcRenderer.invoke("trace:show-results"),
  current: () => ipcRenderer.invoke("trace:current"),
  history: (query) => ipcRenderer.invoke("trace:history", query ?? ""),
  showHistory: (id) => ipcRenderer.invoke("trace:show-history", id),
  openFile: (path, line) => ipcRenderer.invoke("trace:open-file", path, line),
  onStage: (cb) => ipcRenderer.on("trace:stage", (_e: IpcRendererEvent, ev) => cb(ev)),
  onReport: (cb) => ipcRenderer.on("trace:report", (_e: IpcRendererEvent, st) => cb(st)),
  onBackend: (cb) => ipcRenderer.on("trace:backend", (_e: IpcRendererEvent, info) => cb(info)),
  settings: () => ipcRenderer.invoke("trace:settings"),
  pause: (minutes) => ipcRenderer.send("trace:pause", minutes),
  resume: () => ipcRenderer.send("trace:resume"),
};

contextBridge.exposeInMainWorld("trace", api);
