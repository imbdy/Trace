"""Central configuration for the Trace debugging backend.

All settings are environment-overridable; no secrets are hardcoded.
Ollama cloud inference (``gemma4:31b-cloud``) is NOT local-only: any code,
screenshot, or log included in an investigation may be processed through
Ollama's cloud service. The backend minimises what is sent (see
project_inspector.build_model_context) and never intentionally includes
secrets (see project_inspector.redact_secrets).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


DEFAULT_MODEL = "gemma4:31b-cloud"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 3777


@dataclass(frozen=True)
class TraceConfig:
    """Runtime configuration. Use :meth:`from_env` in production code."""

    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_model: str = DEFAULT_MODEL
    # Optional bearer token for Ollama cloud auth. Read ONLY from env.
    ollama_api_key: str | None = None
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 120.0
    list_models_timeout_s: float = 10.0
    max_context_chars: int = 24_000
    max_file_bytes: int = 200_000
    max_screenshot_dim: int = 1280
    diagnostics_timeout_s: float = 30.0
    bind_host: str = DEFAULT_BIND_HOST
    bind_port: int = DEFAULT_BIND_PORT

    @classmethod
    def from_env(cls) -> "TraceConfig":
        return cls(
            ollama_host=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/"),
            ollama_model=os.environ.get("TRACE_MODEL", DEFAULT_MODEL),
            ollama_api_key=os.environ.get("OLLAMA_API_KEY"),
            connect_timeout_s=float(os.environ.get("TRACE_CONNECT_TIMEOUT_S", "5")),
            read_timeout_s=float(os.environ.get("TRACE_READ_TIMEOUT_S", "120")),
            bind_host=os.environ.get("TRACE_BIND_HOST", DEFAULT_BIND_HOST),
            bind_port=int(os.environ.get("TRACE_BIND_PORT", str(DEFAULT_BIND_PORT))),
        )

    def validate(self) -> list[str]:
        """Return a list of problems; empty means valid."""
        problems: list[str] = []
        if self.bind_host not in ("127.0.0.1", "::1", "localhost"):
            problems.append(
                f"bind_host must be loopback-only, got {self.bind_host!r}. "
                "Trace never exposes its server to the network."
            )
        if not self.ollama_host.startswith(("http://", "https://")):
            problems.append(f"ollama_host must be an http(s) URL, got {self.ollama_host!r}")
        if not self.ollama_model.strip():
            problems.append("ollama_model must not be empty")
        return problems

    def redacted(self) -> dict:
        """Config safe to return over the API (no secrets)."""
        return {
            "ollama_host": self.ollama_host,
            "ollama_model": self.ollama_model,
            "has_api_key": bool(self.ollama_api_key),
            "max_context_chars": self.max_context_chars,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "cloud_note": (
                f"Model {self.ollama_model!r} may process submitted context "
                "through Ollama's cloud service; it is not local-only."
            ),
        }
