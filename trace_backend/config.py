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
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "gemma4:31b-cloud"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 3777
# The agent-graph cycle guard: max node steps per investigation. The happy
# path takes ~13; raising the bound only matters if the graph grows.
DEFAULT_MAX_ITERATIONS = 64

# .env files already loaded this process (from_env can be called more than
# once); each candidate file is read at most once per process.
_ENV_FILES_LOADED: set[str] = set()


def _load_env_file(path: Path) -> None:
    """Minimal stdlib KEY=VALUE loader — no third-party dotenv.

    Existing process environment always wins (standard .env behavior),
    values are trimmed and unquoted, and file errors are ignored silently:
    missing/unreadable .env simply means defaults.
    """
    key = str(path)
    if key in _ENV_FILES_LOADED:
        return
    _ENV_FILES_LOADED.add(key)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def _candidate_env_files() -> list[Path]:
    """Where a .env is looked for, first match wins: CWD, then repo root."""
    out: list[Path] = []
    for cand in (Path.cwd() / ".env",
                 Path(__file__).resolve().parent.parent / ".env"):
        if cand not in out:
            out.append(cand)
    return out


@dataclass(frozen=True)
class TraceConfig:
    """Runtime configuration. Use :meth:`from_env` in production code."""

    ollama_host: str = DEFAULT_OLLAMA_HOST
    ollama_model: str = DEFAULT_MODEL
    # Optional bearer token for Ollama cloud auth. Read ONLY from env.
    ollama_api_key: str | None = None
    # Provider mode. OpenAI-compatible covers OpenAI, OpenRouter, Groq,
    # Together, local gateways, and custom providers exposing /chat/completions.
    provider: str = "ollama"
    api_base: str | None = None
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 120.0
    list_models_timeout_s: float = 10.0
    max_context_chars: int = 24_000
    max_file_bytes: int = 200_000
    max_screenshot_dim: int = 1280
    diagnostics_timeout_s: float = 30.0
    bind_host: str = DEFAULT_BIND_HOST
    bind_port: int = DEFAULT_BIND_PORT
    # Investigation history: one small JSON file per finished investigation so
    # a completed verdict survives a restart and can be recalled (and followed
    # up on) without re-running the model. Disable with TRACE_HISTORY=0.
    history_enabled: bool = True
    history_dir: str | None = None  # None → default_history_dir()
    history_max_entries: int = 500
    # Safe auto-fix: when True, a verified unified diff may be written back to
    # files inside the project root (with a .trace-bak backup per file).
    # Unverified diffs are NEVER written. Disable with TRACE_ALLOW_FILE_EDITS=0.
    allow_file_edits: bool = True
    # Test execution is opt-in only (TRACE_ALLOW_TESTS=1): even then, only the
    # allowlisted runner from code_tools.detect_test_command is ever executed.
    # Project scripts are never executed.
    allow_test_execution: bool = False
    # Agent-graph step budget (TRACE_MAX_ITERATIONS): how many node hops one
    # investigation may take before the cycle guard stops it honestly.
    max_iterations: int = DEFAULT_MAX_ITERATIONS

    @classmethod
    def from_env(cls) -> "TraceConfig":
        for env_file in _candidate_env_files():
            _load_env_file(env_file)
        return cls(
            ollama_host=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/"),
            ollama_model=os.environ.get("TRACE_MODEL", DEFAULT_MODEL),
            ollama_api_key=os.environ.get("OLLAMA_API_KEY"),
            provider=os.environ.get("TRACE_PROVIDER", "ollama").strip().lower(),
            api_base=os.environ.get("TRACE_API_BASE") or None,
            connect_timeout_s=float(os.environ.get("TRACE_CONNECT_TIMEOUT_S", "5")),
            read_timeout_s=float(os.environ.get("TRACE_READ_TIMEOUT_S", "120")),
            bind_host=os.environ.get("TRACE_BIND_HOST", DEFAULT_BIND_HOST),
            bind_port=int(os.environ.get("TRACE_BIND_PORT", str(DEFAULT_BIND_PORT))),
            history_enabled=os.environ.get("TRACE_HISTORY", "1").strip().lower()
            not in ("0", "false", "no", "off"),
            history_max_entries=int(os.environ.get("TRACE_HISTORY_MAX", "500")),
            history_dir=os.environ.get("TRACE_HISTORY_DIR") or None,
            allow_file_edits=os.environ.get(
                "TRACE_ALLOW_FILE_EDITS", "1").strip().lower()
            not in ("0", "false", "no", "off"),
            allow_test_execution=os.environ.get(
                "TRACE_ALLOW_TESTS", "0").strip().lower()
            in ("1", "true", "yes", "on"),
            max_iterations=int(os.environ.get(
                "TRACE_MAX_ITERATIONS", str(DEFAULT_MAX_ITERATIONS))),
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
        if self.provider not in ("ollama", "openai-compatible"):
            problems.append("provider must be 'ollama' or 'openai-compatible'")
        if self.provider == "openai-compatible" and not (self.api_base or "").strip():
            problems.append("api_base is required for openai-compatible providers")
        if not (2 <= self.max_iterations <= 1000):
            problems.append(
                f"max_iterations must be between 2 and 1000, got "
                f"{self.max_iterations!r} (TRACE_MAX_ITERATIONS); the happy "
                "path needs ~13 steps")
        return problems

    def redacted(self) -> dict:
        """Config safe to return over the API (no secrets)."""
        return {
            "ollama_host": self.ollama_host,
            "ollama_model": self.ollama_model,
            "has_api_key": bool(self.ollama_api_key),
            "provider": self.provider,
            "api_base": self.api_base or self.ollama_host,
            "max_context_chars": self.max_context_chars,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "allow_file_edits": self.allow_file_edits,
            "allow_test_execution": self.allow_test_execution,
            "max_iterations": self.max_iterations,
            "cloud_note": (
                f"Model {self.ollama_model!r} may process submitted context "
                "through Ollama's cloud service; it is not local-only."
            ),
        }
