"""Application startup/shutdown: backend + companion + results wiring.

Owns the investigation worker thread so the character stays responsive
during long runs. UI updates flow worker -> queue -> Tk `after` pump.
Progress shown is always from real backend SSE events; nothing simulated.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk

from .api_client import ApiError, TraceApiClient
from .companion import Companion
from .results_window import ResultsWindowManager


def _fix_applied_summary(status: dict) -> str | None:
    """Human line when auto_fix already wrote files; else None (show window).

    Returns None when the report window should still open (not auto-fix, or
    the fix was not applied).
    """
    try:
        report = (status or {}).get("report") or {}
        applied = report.get("fix_applied") or {}
        if isinstance(applied, dict) and applied.get("applied"):
            files = ", ".join(applied.get("applied_files", []))
            return (f"Fixed {files or 'the files'} in place "
                    "(backups kept as *.trace-bak).")
    except Exception:
        pass
    return None


class TraceApp:
    def __init__(self, base_url: str = "http://127.0.0.1:3777/api/v1",
                 project_dir: str | None = None,
                 api: TraceApiClient | None = None):
        self.base_url = base_url
        self.project_dir = project_dir
        self.api = api or TraceApiClient(base_url)
        self.root: tk.Tk | None = None
        self.companion: Companion | None = None
        self.results: ResultsWindowManager | None = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._inv_id: str | None = None
        self._closed = False
        self._server = None

    # -- backend ------------------------------------------------------------
    def ensure_backend(self, autostart: bool = True,
                       wait_s: float = 8.0) -> bool:
        """Return True when the loopback backend answers health."""
        if self.api.backend_available():
            return True
        if not autostart:
            return False
        try:
            from trace_backend.config import TraceConfig
            from trace_backend.server import run_server
            from urllib.parse import urlparse
            parts = urlparse(self.base_url)
            host, port = parts.hostname or "127.0.0.1", parts.port or 3777
            if host not in ("127.0.0.1", "localhost", "::1"):
                return False  # never autostart a non-loopback server
            config = TraceConfig(bind_host="127.0.0.1", bind_port=port)
            self._server = run_server(config, block=False)
            t = threading.Thread(target=self._server.serve_forever,
                                 daemon=True, name="trace-backend")
            t.start()
        except Exception:
            return False
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if self.api.backend_available():
                return True
            time.sleep(0.2)
        return False

    # -- lifecycle ----------------------------------------------------------
    def start(self, autostart_backend: bool = True):
        self.root = tk.Tk()
        try:
            from .icons import apply_window_icon, init_process_identity
            init_process_identity()
            try:
                self.root.title("Trace")
            except Exception:
                pass
            apply_window_icon(self.root)
        except Exception:
            pass
        try:
            self.root.withdraw()  # companion is the visible window
        except Exception:
            pass
        ok = self.ensure_backend(autostart=autostart_backend)
        self.results = ResultsWindowManager(self.root, api_client=self.api)
        self.companion = Companion(
            self._companion_root(),
            on_submit=self.submit, on_cancel=self.cancel,
            on_close=self.shutdown)
        if not ok:
            self.companion.set_error(
                "Backend unavailable — run `python main.py --backend-only` "
                "in another terminal, then ask again.")
        self.root.after(60, self._pump)
        try:
            self.root.mainloop()
        finally:
            self._teardown()

    def _companion_root(self) -> tk.Tk:
        win = tk.Toplevel(self.root)
        # Companion manages its own top-level behavior; the hidden Tk root
        # only hosts the event loop + results windows.
        try:
            from .icons import apply_window_icon
            apply_window_icon(win)
        except Exception:
            pass
        return win

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        inv = self._inv_id
        if inv:
            try:
                self.api.cancel_investigation(inv)
            except Exception:
                pass
        try:
            if self.results is not None:
                self.results.close()
        except Exception:
            pass
        self._teardown()
        try:
            if self.root is not None:
                self.root.destroy()
        except Exception:
            pass

    def _teardown(self):
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
        except Exception:
            pass
        finally:
            self._server = None

    # -- investigation flow -------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def submit(self, text: str, auto_fix: bool = False):
        if self.is_running:
            self._ui_queue.put(("note", "Already investigating — cancel first."))
            return
        if not self.api.backend_available():
            if not self.ensure_backend():
                self._ui_queue.put(("error",
                                    "Backend unavailable — start it, then retry."))
                return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run_investigation,
                                        args=(text, bool(auto_fix)), daemon=True,
                                        name="trace-investigation")
        self._worker.start()

    def cancel(self):
        inv = self._inv_id
        self._stop.set()
        if inv:
            try:
                self.api.cancel_investigation(inv)
            except Exception:
                pass

    def _run_investigation(self, text: str, auto_fix: bool = False):
        q = self._ui_queue
        q.put(("running", True))
        try:
            inv_id = self.api.start_investigation({
                "request_text": text,
                "project_dir": self.project_dir,
                "include_screenshot": True,
                "screenshot_mode": "active",
                "include_clipboard": False,
                "auto_fix": bool(auto_fix),
            })
        except ApiError as e:
            q.put(("error", self._present(e)))
            q.put(("running", False))
            return
        self._inv_id = inv_id
        q.put(("started", inv_id))

        def on_event(ev: dict):
            # forward REAL backend events only
            q.put(("event", ev))

        try:
            self.api.stream_events(inv_id, on_event, stop=self._stop)
        except ApiError as e:
            q.put(("error", self._present(e)))
            q.put(("running", False))
            return
        if self._stop.is_set():
            q.put(("event", {"investigation_id": inv_id, "stage": "cancelled",
                             "message": "Cancelled by user.", "progress": 0.0}))
        try:
            status = self.api.get_status(inv_id)
        except ApiError as e:
            q.put(("error", self._present(e)))
            q.put(("running", False))
            return
        q.put(("finished", status))
        q.put(("running", False))
        self._inv_id = None

    @staticmethod
    def _present(err: ApiError) -> str:
        base = str(err)
        if err.code == "backend_unreachable":
            return base
        return f"{base} {err.hint}".strip()

    # -- UI pump (runs on the Tk thread) ------------------------------------
    def _pump(self):
        comp = self.companion
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if comp is None:
                    continue
                if kind == "running":
                    comp.set_running(bool(payload))
                    if payload:
                        comp.set_stage("receive", "Sending request…", 0.0)
                elif kind == "note":
                    comp.stage_text = str(payload)
                elif kind == "error":
                    comp.set_running(False)
                    comp.set_error(str(payload))
                elif kind == "started":
                    comp.stage_text = "Investigation started…"
                elif kind == "event":
                    ev = payload if isinstance(payload, dict) else {}
                    comp.set_stage(str(ev.get("stage", "")),
                                   str(ev.get("message", "")),
                                   float(ev.get("progress", 0.0) or 0.0))
                    if ev.get("stage") in ("done", "error", "cancelled"):
                        comp.set_running(False)
                elif kind == "finished":
                    comp.set_running(False)
                    status = payload if isinstance(payload, dict) else {}
                    state = status.get("status", "")
                    fixed = _fix_applied_summary(status)
                    if state == "done":
                        if fixed:
                            # Auto-fix path: stay quiet, no results window —
                            # the bug is already fixed in place.
                            comp.set_stage("done", fixed, 1.0)
                        else:
                            comp.set_stage("done", "Investigation complete.", 1.0)
                    elif state == "cancelled":
                        comp.set_stage("cancelled", "Cancelled.", 0.0)
                    else:
                        comp.set_error(f"Investigation {state or 'ended unexpectedly'}.")
                    if fixed is None:
                        # Normal path (or auto-fix that did not apply): show
                        # the report window so the user can review / apply.
                        try:
                            if self.results is not None:
                                self.results.show(status)
                                try:
                                    from .icons import apply_window_icon
                                    apply_window_icon(self.results.win)
                                except Exception:
                                    pass
                        except Exception:
                            pass
        except queue.Empty:
            pass
        except Exception:
            pass
        finally:
            try:
                if self.root is not None and not self._closed:
                    self.root.after(60, self._pump)
            except Exception:
                pass
