"""Thin HTTP client for the Trace loopback backend.

Deliberately dumb: request/response plumbing only. No model logic, no prompt
building, no Ollama knowledge — all reasoning lives in the backend. The
renderer must never import this module's internals for anything smarter than
start / stream / cancel / open-file.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request

#: Backend investigation states that end an SSE stream. Owned here (transport
#: knowledge of the API contract) so this module never imports character/UI code.
TERMINAL_STAGES = ("done", "error", "cancelled")


class ApiError(Exception):
    """User-presentable backend failure."""

    def __init__(self, code: str, message: str, hint: str = "",
                 http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.http_status = http_status


class TraceApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:3777/api/v1",
                 timeout_s: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    # -- plumbing -----------------------------------------------------------
    def _request(self, method: str, path: str,
                 body: dict | None = None) -> tuple[int, dict]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                payload = {}
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise ApiError(
                code=err.get("code", f"http_{e.code}"),
                message=err.get("message", f"Backend returned HTTP {e.code}"),
                hint=err.get("hint", ""),
                http_status=e.code,
            ) from e
        except (urllib.error.URLError, ConnectionError, OSError,
                socket.timeout, TimeoutError) as e:
            raise ApiError(
                code="backend_unreachable",
                message=f"Cannot reach the Trace backend at {self.base_url}: {e}",
                hint="Start it with `python main.py --backend-only` (or just "
                     "`python main.py`, which starts it automatically), then retry.",
            ) from e
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            raise ApiError("bad_response",
                           "Backend returned malformed JSON.") from e
        return status, parsed if isinstance(parsed, dict) else {}

    # -- contract -----------------------------------------------------------
    def health(self) -> dict:
        _, payload = self._request("GET", "/health")
        return payload

    def backend_available(self) -> bool:
        try:
            return bool(self.health().get("ok"))
        except ApiError:
            return False

    def start_investigation(self, request: dict) -> str:
        status, payload = self._request("POST", "/investigations", request)
        inv_id = payload.get("investigation_id", "")
        if status not in (200, 202) or not inv_id:
            raise ApiError("bad_response",
                           "Backend did not return an investigation id.")
        return str(inv_id)

    def get_status(self, inv_id: str) -> dict:
        _, payload = self._request("GET", f"/investigations/{inv_id}")
        return payload

    def cancel_investigation(self, inv_id: str) -> dict:
        _, payload = self._request("POST", f"/investigations/{inv_id}/cancel")
        return payload

    def open_file(self, path: str, line: int | None = None) -> dict:
        body = {"path": path, **({"line": line} if line else {})}
        _, payload = self._request("POST", "/open-file", body)
        return payload

    def stream_events(self, inv_id: str, on_event,
                      stop: threading.Event | None = None) -> None:
        """Blocking SSE read. Calls on_event(dict) per event. Returns at the
        terminal event (done/error/cancelled) or when ``stop`` is set.

        Transport errors raise ApiError. Malformed lines are skipped —
        never surfaced as fake progress.
        """
        url = f"{self.base_url}/investigations/{inv_id}/events"
        req = urllib.request.Request(url, method="GET",
                                     headers={"Accept": "text/event-stream"})
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout_s)
        except (urllib.error.URLError, OSError, socket.timeout,
                TimeoutError) as e:
            raise ApiError("backend_unreachable",
                           f"Lost connection to the Trace backend: {e}") from e
        try:
            while True:
                if stop is not None and stop.is_set():
                    return
                line = resp.readline()
                if not line:
                    return
                line = line.decode("utf-8", "replace").strip() \
                    if isinstance(line, bytes) else line.strip()
                if not line or line.startswith(":"):
                    continue  # keepalive / comment
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue  # never surface malformed lines as progress
                if not isinstance(event, dict):
                    continue
                on_event(event)
                if event.get("stage") in TERMINAL_STAGES:
                    return
        finally:
            try:
                resp.close()
            except Exception:
                pass
