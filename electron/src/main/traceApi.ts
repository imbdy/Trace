// Thin loopback HTTP client for the Trace backend. Plumbing only:
// no prompts, no model calls, no Ollama knowledge (the backend owns those).
import {
  InvestigationStatus,
  ProgressEvent,
  StartRequest,
  TERMINAL_STAGES,
} from "../shared/protocol";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public hint = "",
    public httpStatus?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function envelopeMessage(status: number, payload: unknown): { code: string; message: string; hint: string } {
  const err =
    typeof payload === "object" && payload !== null
      ? (payload as { error?: { code?: string; message?: string; hint?: string } }).error
      : undefined;
  return {
    code: err?.code ?? `http_${status}`,
    message: err?.message ?? `Backend returned HTTP ${status}`,
    hint: err?.hint ?? "",
  };
}

export class TraceApi {
  constructor(public baseUrl: string) {}

  private async request(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<{ status: number; payload: Record<string, unknown> }> {
    let res;
    try {
      res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new ApiError(
        "backend_unreachable",
        `Cannot reach the Trace backend at ${this.baseUrl}: ${(e as Error).message}`,
        "Start it with `python main.py --backend-only`, then retry.",
      );
    }
    let payload: Record<string, unknown> = {};
    try {
      payload = (await res.json()) as Record<string, unknown>;
    } catch {
      throw new ApiError("bad_response", "Backend returned malformed JSON.", "", res.status);
    }
    if (!res.ok) {
      const m = envelopeMessage(res.status, payload);
      throw new ApiError(m.code, m.message, m.hint, res.status);
    }
    return { status: res.status, payload };
  }

  async start(req: StartRequest): Promise<string> {
    const { status, payload } = await this.request("POST", "/investigations", req);
    const id = typeof payload.investigation_id === "string" ? payload.investigation_id : "";
    if ((status === 200 || status === 202) && id) return id;
    throw new ApiError("bad_response", "Backend did not return an investigation id.");
  }

  async followUp(parentId: string, requestText: string): Promise<string> {
    const { status, payload } = await this.request(
      "POST", `/investigations/${encodeURIComponent(parentId)}/follow-up`,
      { request_text: requestText },
    );
    const id = typeof payload.investigation_id === "string" ? payload.investigation_id : "";
    if ((status === 200 || status === 202) && id) return id;
    throw new ApiError("bad_response", "Backend did not return a follow-up id.");
  }

  // Plain conversation. Unlike investigations this is synchronous: one
  // request, one prose reply, no stages, no verification anywhere.
  async chat(message: string,
             history: { role: string; content: string }[]):
    Promise<string> {
    const { status, payload } = await this.request("POST", "/chat", {
      message,
      history,
    });
    if (status === 200 && typeof payload.reply === "string" && payload.reply.trim()) {
      return payload.reply;
    }
    throw new ApiError("bad_response", "The backend returned no chat reply.");
  }

  async configureProvider(config: { provider: string; model: string; api_base?: string; api_key?: string }): Promise<Record<string, unknown>> {
    const { payload } = await this.request("POST", "/config", config);
    return payload;
  }

  // Settings-panel "Test connection": the backend fires one minimal "Say OK"
  // round-trip at the provider. The result is {ok, latency_ms?} / {ok: false,
  // error} — HTTP 200 either way; ApiError only for a down backend or an
  // invalid request body.
  async testProviderConnection(config: { provider?: string; model?: string; api_base?: string; api_key?: string }): Promise<Record<string, unknown>> {
    const { payload } = await this.request("POST", "/config/test", config ?? {});
    return payload;
  }

  async providerConfig(): Promise<Record<string, unknown>> {
    const { payload } = await this.request("GET", "/config");
    return payload;
  }

  async getStatus(id: string): Promise<InvestigationStatus> {
    const { payload } = await this.request("GET", `/investigations/${id}`);
    return payload as unknown as InvestigationStatus;
  }

  async history(limit = 50, query = ""): Promise<Record<string, unknown>[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (query.trim()) params.set("q", query.trim());
    const { payload } = await this.request("GET", `/investigations?${params}`);
    return Array.isArray(payload.investigations)
      ? payload.investigations as Record<string, unknown>[] : [];
  }

  async cancel(id: string): Promise<Record<string, unknown>> {
    const { payload } = await this.request("POST", `/investigations/${id}/cancel`);
    return payload;
  }

  async openFile(path: string, line?: number): Promise<Record<string, unknown>> {
    const { payload } = await this.request("POST", "/open-file", { path, ...(line ? { line } : {}) });
    return payload;
  }

  async applyFix(id: string, dryRun = false): Promise<Record<string, unknown>> {
    const { payload } = await this.request(
      "POST", `/investigations/${encodeURIComponent(id)}/apply-fix`, { dry_run: dryRun });
    return payload;
  }

  // Yields REAL backend SSE events; skips malformed lines and keepalives.
  async *events(id: string, signal?: AbortSignal): AsyncGenerator<ProgressEvent> {
    let res;
    try {
      res = await fetch(`${this.baseUrl}/investigations/${id}/events`, {
        headers: { Accept: "text/event-stream" },
        signal,
      });
    } catch (e) {
      throw new ApiError(
        "backend_unreachable",
        `Lost connection to the Trace backend: ${(e as Error).message}`,
      );
    }
    if (!res.ok || !res.body) {
      throw new ApiError("bad_response", `Event stream failed (HTTP ${res.status}).`, "", res.status);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 1);
          if (!line || line.startsWith(":") || !line.startsWith("data:")) continue;
          let ev: unknown;
          try {
            ev = JSON.parse(line.slice(5).trim());
          } catch {
            continue; // never surface malformed lines as progress
          }
          if (typeof ev !== "object" || ev === null) continue;
          const event = ev as ProgressEvent;
          yield event;
          if ((TERMINAL_STAGES as readonly string[]).includes(event.stage)) return;
        }
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        /* ignore */
      }
    }
  }
}
