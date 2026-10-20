"""Dedicated Ollama client. No screenshot/UI/file-scanning logic lives here."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .config import TraceConfig


# --- Errors -----------------------------------------------------------------

class OllamaError(Exception):
    """Base error with a user-facing message and a remediation hint."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class OllamaConnectionError(OllamaError):
    pass


class OllamaTimeoutError(OllamaError):
    pass


class OllamaModelNotFoundError(OllamaError):
    pass


class OllamaAuthError(OllamaError):
    pass


class OllamaCancelledError(OllamaError):
    pass


# --- Client -----------------------------------------------------------------

@dataclass
class OllamaMessage:
    role: str  # system | user | assistant
    content: str
    images: list[str] | None = None  # base64 data URLs or raw base64


class OllamaClient:
    """Thin, testable wrapper over the Ollama REST API.

    Uses only the stdlib (urllib) so the backend has zero mandatory
    third-party dependencies. Streaming is supported via chunked reads
    of the ``/api/chat`` NDJSON stream.
    """

    def __init__(self, config: TraceConfig | None = None,
                 opener: object | None = None):
        self.config = config or TraceConfig.from_env()
        # ``opener`` is an injectable request function for tests:
        #   opener(url, payload, headers, timeout) -> response-like
        self._opener = opener

    # -- helpers ------------------------------------------------------------
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.ollama_api_key:
            headers["Authorization"] = f"Bearer {self.config.ollama_api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.config.ollama_host.rstrip('/')}{path}"

    def _friendly_connection_error(self, exc: Exception) -> OllamaConnectionError:
        return OllamaConnectionError(
            f"Cannot reach Ollama at {self.config.ollama_host}: {exc}. "
            f"Model {self.config.ollama_model!r} is unavailable.",
            hint=(
                "Start Ollama (`ollama serve`), ensure the host is reachable, "
                f"then run `ollama pull {self.config.ollama_model}`. "
                "If a remote/cloud endpoint is required, set OLLAMA_HOST and "
                "OLLAMA_API_KEY accordingly."
            ),
        )

    # -- public API ---------------------------------------------------------
    def check_connection(self, timeout: float | None = None) -> dict:
        """Return ``{"ok": True, "models": [...]}`` or raise OllamaError."""
        timeout = timeout if timeout is not None else self.config.list_models_timeout_s
        url = self._url("/api/tags")
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                return {"ok": True, "models": [m.get("name") for m in data.get("models", [])]}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise OllamaAuthError(
                    "Ollama rejected the request (401/403). Authentication or "
                    "network access may be required for this endpoint.",
                    hint="Check OLLAMA_HOST and set OLLAMA_API_KEY if your "
                         "Ollama endpoint needs a key.",
                ) from e
            raise OllamaError(f"Ollama returned HTTP {e.code}: {e.reason}") from e
        except (urllib.error.URLError, ConnectionError, socket.timeout,
                TimeoutError, OSError) as e:
            raise self._friendly_connection_error(e) from e

    def chat(self,
             messages: list[dict],
             *,
             stream: bool = False,
             on_token: object = None,
             cancel_event: threading.Event | None = None,
             timeout: float | None = None,
             options: dict | None = None) -> str:
        """Call ``POST /api/chat``. Returns the full assistant text.

        ``messages``: list of ``{"role": ..., "content": ..., "images"?: [...]}``.
        When ``stream`` is True, ``on_token`` (callable) receives deltas.
        Raises OllamaCancelledError if ``cancel_event`` is set.
        """
        timeout = timeout if timeout is not None else self.config.read_timeout_s
        payload = {
            "model": self.config.ollama_model,
            "messages": messages,
            "stream": stream,
        }
        if options:
            payload["options"] = options
        return self._post("/api/chat", payload, timeout=timeout,
                          stream=stream, on_token=on_token,
                          cancel_event=cancel_event)

    def generate(self, prompt: str, *, system: str | None = None,
                 images: list[str] | None = None,
                 stream: bool = False, on_token: object = None,
                 cancel_event: threading.Event | None = None,
                 timeout: float | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        user_msg: dict = {"role": "user", "content": prompt}
        if images:
            user_msg["images"] = images
        messages.append(user_msg)
        return self.chat(messages, stream=stream, on_token=on_token,
                         cancel_event=cancel_event, timeout=timeout)

    # -- transport ----------------------------------------------------------
    def _post(self, path: str, payload: dict, *, timeout: float,
              stream: bool, on_token: object,
              cancel_event: threading.Event | None) -> str:
        body = json.dumps(payload).encode("utf-8")
        url = self._url(path)
        req = urllib.request.Request(url, data=body,
                                     headers=self._headers(), method="POST")
        try:
            resp_cm = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code == 404 and "model" in err_body.lower():
                raise OllamaModelNotFoundError(
                    f"Model {self.config.ollama_model!r} not found on "
                    f"{self.config.ollama_host}.",
                    hint=f"Run `ollama pull {self.config.ollama_model}` or set "
                         "TRACE_MODEL to an installed model.",
                ) from e
            if e.code in (401, 403):
                raise OllamaAuthError(
                    "Ollama requires authentication for this endpoint.",
                    hint="Set OLLAMA_API_KEY (env) or use a permitted host.",
                ) from e
            raise OllamaError(
                f"Ollama request failed (HTTP {e.code}): {err_body[:500]}") from e
        except (socket.timeout, TimeoutError) as e:
            raise OllamaTimeoutError(
                f"Ollama request timed out after {timeout}s.",
                hint="The model may be loading; retry, or raise "
                     "TRACE_READ_TIMEOUT_S.",
            ) from e
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            raise self._friendly_connection_error(e) from e

        try:
            if not stream:
                raw = resp_cm.read()
                return self._parse_nonstream(raw)
            return self._read_stream(resp_cm, on_token, cancel_event, timeout)
        except OllamaError:
            raise
        except (socket.timeout, TimeoutError) as e:
            raise OllamaTimeoutError(
                f"Ollama response timed out after {timeout}s.",
                hint="The model may be slow; retry, or raise TRACE_READ_TIMEOUT_S.",
            ) from e
        except (http.client.HTTPException, urllib.error.URLError,
                ConnectionError, OSError) as e:
            # The connection died mid-response (stream reset, dropped socket):
            # surface it as an OllamaError so callers produce an honest
            # low-confidence report instead of an internal error.
            raise self._friendly_connection_error(e) from e
        finally:
            try:
                if hasattr(resp_cm, "close"):
                    resp_cm.close()
            except Exception:
                pass

    @staticmethod
    def _parse_nonstream(raw: bytes) -> str:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as e:
            raise OllamaError(
                f"Ollama returned malformed JSON ({len(raw)} bytes).",
                hint="Retry the request; check the Ollama server version.",
            ) from e
        if isinstance(data, dict):
            msg = data.get("message", {})
            if isinstance(msg, dict) and "content" in msg:
                return str(msg["content"])
            if "response" in data:  # /api/generate shape
                return str(data["response"])
        raise OllamaError(f"Unexpected Ollama response shape: {str(raw)[:300]}")

    def _read_stream(self, resp, on_token, cancel_event,
                     timeout: float) -> str:
        parts: list[str] = []
        deadline = time.monotonic() + timeout
        # resp is an http response; iterate lines (NDJSON).
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise OllamaCancelledError("Ollama request cancelled by user.")
            if time.monotonic() > deadline:
                raise OllamaTimeoutError(
                    f"Ollama streaming timed out after {timeout}s.")
            line = resp.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line.decode("utf-8", "replace")
                                   if isinstance(line, bytes) else line)
            except json.JSONDecodeError:
                continue  # tolerate malformed chunk
            msg = chunk.get("message", {})
            delta = msg.get("content", "") if isinstance(msg, dict) else ""
            if delta:
                parts.append(delta)
                if callable(on_token):
                    try:
                        on_token(delta)
                    except Exception:
                        pass
            if chunk.get("done"):
                break
        return "".join(parts)
