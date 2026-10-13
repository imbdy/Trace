"""Loopback-only HTTP backend for the Trace frontend.

Endpoints (see docs/API_CONTRACT.md):
  GET  /api/v1/health
  GET  /api/v1/config                      (redacted)
  POST /api/v1/investigations               start → {investigation_id}
  GET  /api/v1/investigations               history list, newest-first
  GET  /api/v1/investigations/{id}          status / result (journal fallback)
  GET  /api/v1/investigations/{id}/events   SSE progress stream
  POST /api/v1/investigations/{id}/cancel   cancel a running investigation
  POST /api/v1/investigations/{id}/follow-up  ask a follow-up question
  POST /api/v1/investigations/{id}/apply-fix  apply the verified fix diff
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
from .history import HistoryJournal, default_history_dir
from .investigation_engine import InvestigationCancelled, InvestigationEngine
from .schemas import (
    STAGES,
    InvestigationRequest,
    InvestigationStatus,
    ProgressEvent,
    error_envelope,
    new_investigation_id,
)


class _Job:
    def __init__(self, inv_id: str, request: InvestigationRequest,
                 prior_report: dict | None = None):
        self.id = inv_id
        self.request = request
        # A follow-up job answers a question about an earlier investigation;
        # the engine reuses that report as context instead of re-collecting
        # evidence from scratch.
        self.prior_report = prior_report
        self.status = "running"
        self.stage = "receive"
        self.progress = 0.0
        self.events: list[ProgressEvent] = []
        self.subscribers: list[queue.Queue] = []
        self.report: dict | None = None
        self.error: dict | None = None
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None

    @property
    def is_follow_up(self) -> bool:
        return bool(self.request.follow_up_of) and self.prior_report is not None


class InvestigationStore:
    def __init__(self, engine: InvestigationEngine,
                 config: TraceConfig | None = None):
        self.engine = engine
        self.jobs: dict[str, _Job] = {}
        self.lock = threading.Lock()
        # History is a convenience, never a dependency: when it is disabled or
        # unavailable, every method below still behaves (recall → 404).
        self.history: HistoryJournal | None = None
        if config is not None and config.history_enabled:
            try:
                self.history = HistoryJournal(
                    config.history_dir or default_history_dir(),
                    max_entries=config.history_max_entries)
            except Exception:
                self.history = None

    def start(self, request: InvestigationRequest,
              prior_report: dict | None = None) -> _Job:
        inv_id = new_investigation_id()
        job = _Job(inv_id, request, prior_report)
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

        def stage_cb(stage: str, message: str):
            idx = STAGES.index(stage) if stage in STAGES else 0
            cb(ProgressEvent(job.id, stage, message, (idx + 1) / len(STAGES)))

        try:
            if job.is_follow_up:
                report = self.engine.follow_up(
                    job.request, job.prior_report, stage_cb,
                    job.cancel, investigation_id=job.id)
            else:
                report = self.engine.investigate(
                    job.request, progress_cb=cb,
                    cancel_event=job.cancel, investigation_id=job.id)
            job.report = report.to_dict()
            if job.request.follow_up_of and not job.report.get("follow_up_of"):
                # The request asked for a follow-up: keep the link visible even
                # if the engine had to fall back to the first-pass path.
                job.report["follow_up_of"] = job.request.follow_up_of
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
            self._record(job)

    def _record(self, job: _Job) -> None:
        """Persist the final verdict so it survives a restart."""
        if self.history is None:
            return
        report = job.report or {}
        self.history.record(job.id, {
            "investigation_id": job.id,
            "status": job.status,
            "progress": job.progress,
            "stage": job.stage,
            "created_at": report.get("created_at") or time.time(),
            "report": job.report,
            "error": job.error,
            "request": {
                "request_text_preview": (job.request.request_text or "")[:300],
                "project_dir": job.request.project_dir or "",
                "follow_up_of": job.request.follow_up_of or "",
            },
        })

    def get(self, inv_id: str) -> _Job | None:
        with self.lock:
            return self.jobs.get(inv_id)

    def list_history(self, limit: int = 50) -> list[dict]:
        """Newest-first summaries of past investigations (may be empty)."""
        if self.history is None:
            return []
        return self.history.list(limit=limit)

    def recall(self, inv_id: str) -> InvestigationStatus | None:
        """A finished investigation that is no longer in memory.

        Returns ``None`` when it was never recorded (or history is off), so the
        caller answers 404 honestly instead of inventing a verdict.
        """
        if self.history is None:
            return None
        payload = self.history.get(inv_id)
        if payload is None:
            return None
        report = payload.get("report")
        error = payload.get("error")
        return InvestigationStatus(
            investigation_id=payload.get("investigation_id") or inv_id,
            status=payload.get("status", "done"),
            progress=float(payload.get("progress") or 1.0),
            stage=payload.get("stage", "report"),
            report=report if isinstance(report, dict) else None,
            error=error if isinstance(error, dict) else None)

    def resolve_report(self, inv_id: str) -> dict | None:
        """The report for ``inv_id``: in-memory job first, then the journal."""
        job = self.get(inv_id)
        if job is not None and job.report is not None:
            return job.report
        if job is not None and job.status == "running":
            return None  # it exists but has no verdict yet
        recalled = self.recall(inv_id)
        return recalled.report if recalled is not None else None

    def request_context(self, inv_id: str) -> dict:
        """The recorded request (project_dir etc.) for a past investigation."""
        job = self.get(inv_id)
        if job is not None:
            return {"request_text": job.request.request_text,
                    "project_dir": job.request.project_dir or "",
                    "follow_up_of": job.request.follow_up_of or ""}
        if self.history is None:
            return {}
        payload = self.history.get(inv_id) or {}
        req = payload.get("request")
        return req if isinstance(req, dict) else {}

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

        def _history_list(self, query: str) -> dict:
            """GET /api/v1/investigations?limit=N — newest-first summaries."""
            limit = 50
            needle = ""
            try:
                params = urllib.parse.parse_qs(query)
                raw = params.get("limit", [""])[0]
                needle = params.get("q", [""])[0].strip().lower()
                if raw:
                    limit = int(raw)
            except ValueError:
                limit = 50  # a bogus limit must never break the listing
            entries = store.list_history(500 if needle else limit)
            if needle:
                entries = [entry for entry in entries if needle in " ".join(
                    str(entry.get(key, "")) for key in
                    ("summary", "request_text_preview", "project_dir")
                ).lower()]
            return {"investigations": entries, "count": len(entries)}

        # -- routes -------------------------------------------------------
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/v1/health":
                return self._send_json({"ok": True, "service": "trace",
                                        "time": time.time()})
            if path == "/api/v1/config":
                return self._send_json(config.redacted())
            if path == "/api/v1/investigations":
                return self._send_json(self._history_list(parsed.query))
            parts = path.split("/")
            # /api/v1/investigations/{id}[/events]
            if len(parts) == 5 and parts[1:4] == ["api", "v1", "investigations"]:
                job = store.get(parts[4])
                if job is not None:
                    status = InvestigationStatus(
                        investigation_id=job.id, status=job.status,
                        progress=job.progress, stage=job.stage,
                        report=job.report, error=job.error)
                    return self._send_json(status.to_dict())
                # Not in memory: fall back to the journal so a verdict stays
                # recallable after a restart. Absent there → honest 404.
                recalled = store.recall(parts[4])
                if recalled is None:
                    return self._send_json(error_envelope(
                        "not_found", f"Unknown investigation {parts[4]}"), 404)
                return self._send_json(recalled.to_dict())
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
                # follow_up_of in the body is equivalent to the follow-up
                # route: resolve the parent, or refuse honestly.
                prior = store.resolve_report(req.follow_up_of) \
                    if req.follow_up_of else None
                if req.follow_up_of and prior is None:
                    return self._send_json(error_envelope(
                        "not_found",
                        f"No completed report for {req.follow_up_of} "
                        "to follow up on"), 404)
                job = store.start(req, prior_report=prior)
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
            if (len(parts) == 6 and parts[1:4] == ["api", "v1", "investigations"]
                    and parts[5] == "follow-up"):
                return self._start_follow_up(parts[4])
            if (len(parts) == 6 and parts[1:4] == ["api", "v1", "investigations"]
                    and parts[5] == "apply-fix"):
                return self._apply_fix(parts[4])
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

        def _start_follow_up(self, parent_id: str):
            """POST /api/v1/investigations/{id}/follow-up → 202.

            Re-asks the model about an existing verdict. The parent report is
            resolved from memory first and the journal second, so a follow-up
            also works after a restart. The new investigation links back with
            ``follow_up_of``.
            """
            data = self._read_json()
            prior = store.resolve_report(parent_id)
            if prior is None:
                return self._send_json(error_envelope(
                    "not_found",
                    f"No completed report for {parent_id} to follow up on"), 404)
            # Keep the parent's project unless the caller overrides it: the
            # follow-up still verifies locations against the same tree.
            project_dir = str(data.get("project_dir") or "") \
                or store.request_context(parent_id).get("project_dir") or None
            req = InvestigationRequest.from_dict({
                "request_text": data.get("request_text", ""),
                "project_dir": project_dir,
                "include_screenshot": False,
                "include_clipboard": False,
                "follow_up_of": parent_id,
            })
            problems = req.validate()
            if problems:
                return self._send_json(error_envelope(
                    "bad_request", "; ".join(problems)), 400)
            job = store.start(req, prior_report=prior)
            return self._send_json({"investigation_id": job.id,
                                    "follow_up_of": parent_id}, 202)

        def _apply_fix(self, inv_id: str):
            """POST /api/v1/investigations/{id}/apply-fix → 200.

            Applies the completed report's VERIFIED fix diff straight to the
            files on disk (with .trace-bak backups). Unverified diffs are
            refused with 409, never force-applied. Body: {"dry_run": bool}.
            """
            data = self._read_json()
            dry_run = bool(data.get("dry_run", False))
            report = store.resolve_report(inv_id)
            if report is None:
                job = store.get(inv_id)
                if job is not None and job.status == "running":
                    return self._send_json(error_envelope(
                        "bad_state",
                        "Investigation is still running; wait for it to finish "
                        "before applying its fix."), 409)
                return self._send_json(error_envelope(
                    "not_found",
                    f"No completed report for {inv_id} to fix from"), 404)
            project_dir = store.request_context(inv_id).get("project_dir") or None
            outcome = store.engine.apply_fix(report, project_dir,
                                             dry_run=dry_run)
            if outcome.get("ok"):
                return self._send_json(outcome, 200)
            reason = str(outcome.get("reason", ""))
            if "verif" in reason.lower() or "running" in reason.lower():
                return self._send_json(error_envelope(
                    "bad_state", reason,
                    "Only a verified diff can be applied; re-run the "
                    "investigation if the files changed."), 409)
            if "disabled" in reason.lower():
                return self._send_json(error_envelope(
                    "forbidden", reason,
                    "Set TRACE_ALLOW_FILE_EDITS=1 to enable safe auto-fix."), 403)
            if "no fix" in reason.lower():
                return self._send_json(error_envelope(
                    "bad_state", reason), 409)
            return self._send_json(error_envelope(
                "bad_state", reason or "Fix could not be applied.",
                "; ".join(outcome.get("warnings", []) or "")), 409)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    return Handler


def _apply_background_identity() -> None:
    """Give the background backend process the Trace identity (best-effort).

    Inline + stdlib-only on purpose: server.py must stay a thin host and
    must never import trace_ui (see tests/test_layering.py). Sets the
    AppUserModelID (taskbar grouping), the console title, and the console
    window icon from assets/icons/trace.ico so ``python main.py
    --backend-only`` also carries the Trace icon.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Trace.Trace.0.1")
        except Exception:
            pass
        try:
            ctypes.windll.kernel32.SetConsoleTitleW("Trace Backend")
        except Exception:
            pass
        try:
            ico = Path(__file__).resolve().parent.parent / "assets" / "icons" / "trace.ico"
            if ico.is_file():
                hwnd = ctypes.windll.kernel32.GetConsoleWindow()
                if hwnd:
                    hicon = ctypes.windll.user32.LoadImageW(
                        None, str(ico), 1, 0, 0, 0x10 | 0x40)
                    if hicon:
                        ctypes.windll.user32.SendMessageW(hwnd, 0x80, 0, hicon)
                        ctypes.windll.user32.SendMessageW(hwnd, 0x80, 1, hicon)
        except Exception:
            pass
    except Exception:
        pass


def run_server(config: TraceConfig | None = None, *,
               engine: InvestigationEngine | None = None,
               block: bool = True) -> ThreadingHTTPServer:
    """Bind loopback-only and serve. Raises if config is not loopback."""
    config = config or TraceConfig.from_env()
    problems = config.validate()
    if problems:
        raise ValueError("Invalid Trace configuration: " + "; ".join(problems))
    _apply_background_identity()
    engine = engine or InvestigationEngine(config)
    store = InvestigationStore(engine, config)
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
