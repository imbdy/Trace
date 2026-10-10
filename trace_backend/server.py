"""Loopback-only HTTP backend for the Trace frontend.

Endpoints (see docs/API_CONTRACT.md):
  GET  /api/v1/health
  GET  /api/v1/config                      (redacted)
  POST /api/v1/investigations               start → {investigation_id}
  GET  /api/v1/investigations/{id}          status / result
  GET  /api/v1/investigations/{id}/events   SSE progress stream
  POST /api/v1/investigations/{id}/cancel   cancel a running investigation
  POST /api/v1/open-file                    {path, line?} → OS open

Binds to 127.0.0.1 only. Never expose to the network.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import TraceConfig
from .investigation_engine import InvestigationCancelled, InvestigationEngine
from .schemas import (
    InvestigationRequest,
    InvestigationStatus,
    ProgressEvent,
    error_envelope,
    new_investigation_id,
)


class _Job:
    def __init__(self, inv_id: str, request: InvestigationRequest):
        self.id = inv_id
        self.request = request
        self.status = "running"
        self.stage = "receive"
        self.progress = 0.0
        self.events: list[ProgressEvent] = []
        self.subscribers: list[queue.Queue] = []
        self.report: dict | None = None
        self.error: dict | None = None
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None


class InvestigationStore:
    def __init__(self, engine: InvestigationEngine):
        self.engine = engine
        self.jobs: dict[str, _Job] = {}
        self.lock = threading.Lock()

    def start(self, request: InvestigationRequest) -> _Job:
        inv_id = new_investigation_id()
        job = _Job(inv_id, request)
        with self.lock:
            self.jobs[inv_id] = job
        t = threading.Thread(target=self._run, args=(job,), daemon=True,
                             name=f"trace-{inv_id}")
        job.thread = t
        t.start()
        return job

    def _publish(self, job: _Job, ev: ProgressEvent):
        job.stage = ev.stage
        job.progress = ev.progress
        job.events.append(ev)
        for q in list(job.subscribers):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    def _run(self, job: _Job):
        def cb(ev: ProgressEvent):
            self._publish(job, ev)

        try:
            report = self.engine.investigate(
                job.request, progress_cb=cb,
                cancel_event=job.cancel, investigation_id=job.id)
            job.report = report.to_dict()
            if job.cancel.is_set():
                job.status = "cancelled"
            else:
                job.status = "done"
                job.stage = "report"
                job.progress = 1.0
        except InvestigationCancelled as e:
            job.status = "cancelled"
            job.error = {"code": "cancelled", "message": str(e)}
        except Exception as e:  # never leak a traceback to a hung job
            job.status = "error"
            job.error = {"code": "internal", "message": f"{type(e).__name__}: {e}"}
        finally:
            done = ProgressEvent(job.id, job.status,
                                 f"Investigation {job.status}.", job.progress)
            job.events.append(done)
            for q in list(job.subscribers):
                try:
                    q.put_nowait(done)
                    q.put_nowait(None)  # stream terminator
                except queue.Full:
                    pass

    def get(self, inv_id: str) -> _Job | None:
        with self.lock:
            return self.jobs.get(inv_id)

    def cancel(self, inv_id: str) -> bool:
        job = self.get(inv_id)
        if job is None or job.status != "running":
            return False
        job.cancel.set()
        return True


def open_file_in_editor(path: str, line: int | None = None) -> dict:
    """Open ``path`` with the OS default handler. Returns {ok, ...}."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"File not found: {path}"}
    try:
        if sys.platform.startswith("win"):
            import os as _os
            _os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        # Line navigation depends on editor integration; report honestly.
        return {"ok": True, "path": str(p), "line": line,
                "note": "Opened with OS default handler. Line navigation requires "
                        "a supported editor integration (not guaranteed)."}
    except Exception as e:
        return {"ok": False, "error": f"Could not open {path}: {e}"}


def make_handler(store: InvestigationStore, config: TraceConfig):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Trace/0.1"

        def log_message(self, *args):  # quieter default logging
            pass

        # -- helpers ------------------------------------------------------
        def _send_json(self, obj: dict | list, status: int = 200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}

        # -- routes -------------------------------------------------------
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/v1/health":
                return self._send_json({"ok": True, "service": "trace",
                                        "time": time.time()})
            if path == "/api/v1/config":
                return self._send_json(config.redacted())
            parts = path.split("/")
            # /api/v1/investigations/{id}[/events]
            if len(parts) == 5 and parts[1:4] == ["api", "v1", "investigations"]:
                job = store.get(parts[4])
                if job is None:
                    return self._send_json(error_envelope(
                        "not_found", f"Unknown investigation {parts[4]}"), 404)
                status = InvestigationStatus(
                    investigation_id=job.id, status=job.status,
                    progress=job.progress, stage=job.stage,
                    report=job.report, error=job.error)
                return self._send_json(status.to_dict())
            if (len(parts) == 6 and parts[1:4] == ["api", "v1", "investigations"]
                    and parts[5] == "events"):
                return self._stream_events(parts[4])
            return self._send_json(error_envelope("not_found", "Unknown route"), 404)

        def _stream_events(self, inv_id: str):
            job = store.get(inv_id)
            if job is None:
                return self._send_json(error_envelope(
                    "not_found", f"Unknown investigation {inv_id}"), 404)
            q: queue.Queue = queue.Queue(maxsize=200)
            job.subscribers.append(q)
            try:
                for ev in list(job.events):
                    q.put_nowait(ev)
                if job.status in ("done", "error", "cancelled"):
                    q.put_nowait(None)
            except queue.Full:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    try:
                        item = q.get(timeout=25)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    if item is None:
                        break
                    if isinstance(item, ProgressEvent):
                        self.wfile.write(item.to_sse().encode("utf-8"))
                        self.wfile.flush()
                    if item.stage in ("done", "error", "cancelled"):
                        # terminal event already queued; terminator follows
                        continue
            except (ConnectionError, BrokenPipeError):
                pass
            finally:
                try:
                    job.subscribers.remove(q)
                except ValueError:
                    pass

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/v1/investigations":
                data = self._read_json()
                req = InvestigationRequest.from_dict(data)
                problems = req.validate()
                if problems:
                    return self._send_json(error_envelope(
                        "bad_request", "; ".join(problems)), 400)
                job = store.start(req)
                return self._send_json({"investigation_id": job.id}, 202)
            parts = path.split("/")
            if (len(parts) == 6 and parts[1:4] == ["api", "v1", "investigations"]
                    and parts[5] == "cancel"):
                ok = store.cancel(parts[4])
                if not ok:
                    job = store.get(parts[4])
                    if job is None:
                        return self._send_json(error_envelope(
                            "not_found", "Unknown investigation"), 404)
                    return self._send_json(error_envelope(
                        "bad_state", f"Investigation is {job.status}, cannot cancel"), 409)
                return self._send_json({"ok": True, "investigation_id": parts[4]})
            if path == "/api/v1/open-file":
                data = self._read_json()
                target = str(data.get("path", ""))
                line = data.get("line")
                if not target:
                    return self._send_json(error_envelope(
                        "bad_request", "Missing 'path'"), 400)
                result = open_file_in_editor(
                    target, int(line) if isinstance(line, int) else None)
                return self._send_json(result,
                                       200 if result.get("ok") else 404)
            return self._send_json(error_envelope("not_found", "Unknown route"), 404)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    return Handler


def run_server(config: TraceConfig | None = None, *,
               engine: InvestigationEngine | None = None,
               block: bool = True) -> ThreadingHTTPServer:
    """Bind loopback-only and serve. Raises if config is not loopback."""
    config = config or TraceConfig.from_env()
    problems = config.validate()
    if problems:
        raise ValueError("Invalid Trace configuration: " + "; ".join(problems))
    engine = engine or InvestigationEngine(config)
    store = InvestigationStore(engine)
    handler = make_handler(store, config)
    server = ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
    server.daemon_threads = True
    if block:
        print(f"Trace backend on http://{config.bind_host}:{config.bind_port} "
              f"(model: {config.ollama_model}) — loopback only. Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return server


if __name__ == "__main__":
    run_server()
