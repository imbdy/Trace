// Trace preload bridge: the ONLY API the renderer sees. Neo-shaped channels
// (resize/hide) plus Trace investigation channels. No Node, no Electron, no
// backend URLs, no model access in the renderer.
import { contextBridge, ipcRenderer, IpcRendererEvent } from "electron";

export interface TraceBridge {
  resize: (w: number, h: number) => void;
  hide: () => void;
  submit: (text: string, opts?: Record<string, unknown>) => Promise<{ ok: boolean; id?: string; reason?: string }>;
  followUp: (parentId: string, text: string) => Promise<{ ok: boolean; id?: string; reason?: string }>;
  chat: (message: string, history?: { role: string; content: string }[]) =>
    Promise<{ ok: boolean; reply?: string; reason?: string; error?: string }>;
  getProviderConfig: () => Promise<Record<string, unknown>>;
  configureProvider: (config: { provider: string; model: string; api_base?: string; api_key?: string }) =>
    Promise<Record<string, unknown>>;
  cancel: () => Promise<{ ok: boolean }>;
  applyFix: (id: string, dryRun?: boolean) => Promise<Record<string, unknown>>;
  showResults: () => Promise<{ ok: boolean }>;
  current: () => Promise<{ id: string | null }>;
  history: (query?: string) => Promise<Record<string, unknown>[] | null>;
  showHistory: (id: string) => Promise<{ ok: boolean; reason?: string }>;
  openFile: (path: string, line?: number) => Promise<Record<string, unknown>>;
  saveReport: (status: unknown) => Promise<{ ok: boolean; path?: string; reason?: string; error?: string }>;
  onStage: (cb: (ev: { investigation_id?: string; stage: string; message: string; progress: number }) => void) => void;
  onReport: (cb: (status: unknown) => void) => void;
  onBackend: (cb: (info: { ok: boolean; message: string }) => void) => void;
}

const api: TraceBridge = {
  resize: (w, h) => ipcRenderer.send("trace:resize", w, h),
  hide: () => ipcRenderer.send("trace:hide"),
  submit: (text, opts) => ipcRenderer.invoke("trace:submit", text, opts ?? {}),
  followUp: (parentId, text) => ipcRenderer.invoke("trace:follow-up", parentId, text),
  chat: (message, history) => ipcRenderer.invoke("trace:chat", message, history ?? []),
  getProviderConfig: () => ipcRenderer.invoke("trace:get-provider-config"),
  configureProvider: (config) => ipcRenderer.invoke("trace:configure-provider", config),
  cancel: () => ipcRenderer.invoke("trace:cancel"),
  applyFix: (id, dryRun) => ipcRenderer.invoke("trace:apply-fix", id, !!dryRun),
  showResults: () => ipcRenderer.invoke("trace:show-results"),
  current: () => ipcRenderer.invoke("trace:current"),
  history: (query) => ipcRenderer.invoke("trace:history", query ?? ""),
  showHistory: (id) => ipcRenderer.invoke("trace:show-history", id),
  openFile: (path, line) => ipcRenderer.invoke("trace:open-file", path, line),
  saveReport: (status) => ipcRenderer.invoke("trace:save-report", status),
  onStage: (cb) => ipcRenderer.on("trace:stage", (_e: IpcRendererEvent, ev) => cb(ev)),
  onReport: (cb) => ipcRenderer.on("trace:report", (_e: IpcRendererEvent, st) => cb(st)),
  onBackend: (cb) => ipcRenderer.on("trace:backend", (_e: IpcRendererEvent, info) => cb(info)),
};

contextBridge.exposeInMainWorld("trace", api);
