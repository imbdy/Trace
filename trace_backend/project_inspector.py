"""Safe project inspection: root detection, file reading, error parsing, diagnostics.

Safety rules:
- Never read outside the detected project root (path traversal rejected).
- Skip .git, dependency dirs, build outputs, venvs, caches (EXCLUDE_DIRS).
- Only read allowlisted text extensions (ALLOWED_EXTENSIONS) + known config names.
- Never execute project scripts. Diagnostics run ONLY allowlisted CLI tools
  that are already installed, with timeouts, and output is truncated.
- Secrets are redacted before anything is sent to the model.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


EXCLUDE_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "bower_components", "vendor",
    "__pycache__", ".venv", "venv", ".tox", ".nox",
    "build", "dist", "out", "target", ".gradle",
    ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".idea", ".vscode", ".vs",
    "coverage", ".coverage", "htmlcov",
    ".next", ".nuxt", ".parcel-cache",
}

ALLOWED_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".scala", ".go", ".rs", ".c", ".h",
    ".cpp", ".hpp", ".cc", ".cs", ".rb", ".php", ".swift", ".m",
    ".html", ".htm", ".css", ".scss", ".json", ".toml", ".yaml",
    ".yml", ".xml", ".ini", ".cfg", ".sh", ".bash", ".sql", ".md",
    ".txt", ".log", ".gradle", ".properties", ".env.example",
}

CONFIG_FILENAMES = {
    "pyproject.toml", "package.json", "cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "requirements.txt", "setup.py", "setup.cfg",
    "makefile", "dockerfile", "tox.ini", "pytest.ini",
}

ROOT_MARKERS = [
    ".git", "pyproject.toml", "setup.py", "package.json", "cargo.toml",
    "go.mod", "pom.xml", "build.gradle", ".sln", "makefile", "Makefile",
]

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|pwd|bearer|token)\s*[:=]\s*['\"]?([^\s'\"]{4,})"),
    re.compile(r"(?i)(aws_secret_access_key|aws_access_key_id)\s*[:=]\s*['\"]?([^\s'\"]+)"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[bap]- [A-Za-z0-9-]+"),
]

MAX_CANDIDATE_FILES = 60
MAX_RELATED_DEPTH = 2


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in path.parts)


def is_readable_name(path: Path) -> bool:
    if path.suffix.lower() in ALLOWED_EXTENSIONS:
        return True
    return path.name.lower() in CONFIG_FILENAMES


def find_project_root(start: str | Path | None) -> Path | None:
    """Walk up from ``start`` (or CWD) looking for project markers."""
    base = Path(start).resolve() if start else Path.cwd().resolve()
    if base.is_file():
        base = base.parent
    if not base.exists():
        return None
    for candidate in [base, *base.parents]:
        for marker in ROOT_MARKERS:
            if (candidate / marker).exists():
                return candidate
        # *.sln glob (next with a default never raises StopIteration).
        try:
            if next(candidate.glob("*.sln"), None) is not None:
                return candidate
        except OSError:
            pass
    return base if base.is_dir() else None


def resolve_within_root(root: Path, target: str | Path) -> Path | None:
    """Resolve ``target`` and ensure it stays inside ``root``. None if unsafe."""
    try:
        resolved = (root / target).resolve() if not Path(target).is_absolute() \
            else Path(target).resolve()
    except (OSError, RuntimeError):
        return None
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        # Allow the root itself.
        if resolved != root.resolve():
            return None
    return resolved


def redact_secrets(text: str) -> str:
    redacted = text
    for pat in SECRET_PATTERNS:
        redacted = pat.sub(lambda m: m.group(0)[: min(8, len(m.group(0)))] + "…[REDACTED]", redacted)
    return redacted


def safe_read(root: Path, target: str | Path,
              max_bytes: int = 200_000) -> tuple[str | None, str]:
    """Read a file safely. Returns (content_or_None, note)."""
    resolved = resolve_within_root(root, target)
    if resolved is None:
        return None, f"rejected: {target} is outside project root {root}"
    if not resolved.is_file():
        return None, f"not found: {target}"
    if is_excluded(resolved.relative_to(root.resolve())
                   if resolved != root.resolve() else Path(".")):
        return None, f"excluded directory: {target}"
    if not is_readable_name(resolved) and resolved.suffix:
        return None, f"skipped non-text extension: {resolved.suffix}"
    try:
        size = resolved.stat().st_size
    except OSError as e:
        return None, f"unreadable: {e}"
    if size > max_bytes:
        return None, f"too large ({size} bytes > {max_bytes}); skipped"
    try:
        data = resolved.read_bytes()
    except OSError as e:
        return None, f"unreadable: {e}"
    if b"\x00" in data[:8192]:
        return None, "binary file; skipped"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return text, f"ok ({len(text)} chars)"


# --- error extraction -------------------------------------------------------

PY_TB = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\S+))?')
GENERIC_LOC = re.compile(r"([\w\-./\\]+\.(?:py|js|ts|tsx|jsx|java|go|rs|cpp|c|cs|rb|php|swift|kt))[:\(](\d+)")

ERROR_HEADER = re.compile(
    r"(?im)^(.*?(Error|Exception|Traceback|FAILED|failed|panic|error[:\s]).*)$")


@dataclass
class StackFrame:
    file: str
    line: int
    func: str | None = None


@dataclass
class ErrorInfo:
    message: str = ""
    frames: list[StackFrame] = field(default_factory=list)
    raw_excerpt: str = ""


def extract_error_info(text: str | None) -> ErrorInfo:
    if not text or not text.strip():
        return ErrorInfo()
    lines = text.strip().splitlines()
    # Error message: last non-empty line for tracebacks, else first error-ish line.
    message = ""
    for line in reversed(lines[-15:]):
        if line.strip():
            message = line.strip()[:1000]
            break
    header = ERROR_HEADER.search(text)
    if header and len(message) < 10:
        message = header.group(1).strip()[:1000]
    frames: list[StackFrame] = []
    for m in PY_TB.finditer(text):
        try:
            frames.append(StackFrame(file=m.group(1), line=int(m.group(2)),
                                     func=m.group(3)))
        except ValueError:
            pass
    if not frames:
        for m in GENERIC_LOC.finditer(text):
            try:
                frames.append(StackFrame(file=m.group(1), line=int(m.group(2))))
            except ValueError:
                pass
            if len(frames) >= 20:
                break
    tail = "\n".join(lines[-40:])
    return ErrorInfo(message=message, frames=frames, raw_excerpt=tail[-4000:])


# --- candidate file ranking --------------------------------------------------

def list_candidate_files(root: Path, query: str = "",
                         limit: int = MAX_CANDIDATE_FILES) -> list[Path]:
    """Rank project files by filename overlap with the query; bounded walk."""
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query or "") if len(t) > 2]
    scored: list[tuple[int, float, Path]] = []
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        # Keep walk bounded for huge repos.
        dirnames.sort()
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if is_excluded(rel) or not is_readable_name(p):
                continue
            try:
                if p.stat().st_size > 500_000:
                    continue
            except OSError:
                continue
            name = fn.lower()
            score = 0
            for tok in tokens:
                if tok in name:
                    score += 3
                if tok in str(rel).lower():
                    score += 1
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0
            scored.append((score, mtime, p))
            count += 1
            if count > 20_000:
                break
        if count > 20_000:
            break
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [p for _, _, p in scored[:limit]]


IMPORT_PATTERNS = [
    re.compile(r"(?m)^\s*(?:from|import)\s+([\w.]+)"),
    re.compile(r"(?m)^\s*import\s+['\"]([\w./-]+)['\"]"),
    re.compile(r"(?m)(?:import|from)\s+['\"]([\w./@-]+)['\"]"),
    re.compile(r'(?m)require\(\s*[\'"]([\w./-]+)[\'"]\s*\)'),
]


def follow_imports(source: Path, root: Path,
                   max_results: int = 10) -> list[Path]:
    """Best-effort related-file discovery from import statements (depth 1)."""
    try:
        text = source.read_text(encoding="utf-8", errors="replace")[:50_000]
    except OSError:
        return []
    candidates: list[Path] = []
    for pat in IMPORT_PATTERNS:
        for m in pat.finditer(text):
            mod = m.group(1).strip()
            for trial in _module_to_trials(mod, source, root):
                if trial.is_file() and not is_excluded(
                        trial.relative_to(root) if _inside(trial, root) else Path(trial.name)):
                    if trial not in candidates:
                        candidates.append(trial)
                if len(candidates) >= max_results:
                    return candidates
    return candidates


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _module_to_trials(mod: str, source: Path, root: Path) -> list[Path]:
    """Candidate files for an import string (relative first, then rooted)."""
    trials: list[Path] = []
    if mod.startswith(("./", "../")):
        # Explicit relative path (JS/TS style): resolve against the importer.
        base = source.parent
        for ext in (".ts", ".tsx", ".js", ".jsx", ".py"):
            trials.append((base / mod).with_suffix(ext))
        return trials
    if mod.startswith("."):
        # Dotted relative import (Python style): resolve against the importer.
        base = source.parent
        rel = mod.lstrip(".").replace(".", "/")
        trials += [base / (rel + ".py"), base / rel / "__init__.py",
                   base / (rel + ".ts"), base / (rel + ".js")]
        return trials
    # Bare module: same-dir first, then root-relative.
    rel = mod.replace(".", "/")
    for base in (source.parent, root):
        for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"):
            trials.append(base / (rel + ext))
        trials.append(base / rel / "__init__.py")
    return trials


# --- diagnostics (allowlisted CLI tools only) --------------------------------

@dataclass
class DiagnosticResult:
    tool: str
    ok: bool
    output: str
    skipped_reason: str = ""


def _run(cmd: list[str], cwd: Path, timeout_s: float) -> str:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                              text=True, timeout=timeout_s)
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return out[-6000:]
    except subprocess.TimeoutExpired:
        return f"[{cmd[0]} timed out after {timeout_s}s]"
    except Exception as e:
        return f"[{cmd[0]} failed: {e}]"


def collect_diagnostics(root: Path, target_files: list[Path],
                        timeout_s: float = 30.0) -> list[DiagnosticResult]:
    """Run whichever of the allowlisted tools are installed. Never runs project code."""
    results: list[DiagnosticResult] = []
    suffixes = {p.suffix.lower() for p in target_files}

    def have(tool: str) -> bool:
        return shutil.which(tool) is not None

    def add(tool: str, cmd: list[str]):
        out = _run(cmd, root, timeout_s)
        results.append(DiagnosticResult(tool=tool, ok=True, output=out))

    # Python: compile-check target files only (no execution).
    if ".py" in suffixes and have("python"):
        for p in target_files[:5]:
            if p.suffix == ".py":
                try:
                    rel = p.relative_to(root)
                except ValueError:
                    continue
                add(f"py_compile:{rel}", ["python", "-m", "py_compile", str(rel)])
    if have("ruff"):
        add("ruff", ["ruff", "check", "."])
    if have("mypy") and ".py" in suffixes:
        add("mypy", ["mypy", "."])
    if have("tsc") and suffixes & {".ts", ".tsx", ".js", ".jsx"}:
        add("tsc", ["tsc", "--noEmit"])
    if have("eslint") and suffixes & {".ts", ".tsx", ".js", ".jsx"}:
        add("eslint", ["eslint", "."])
    if (root / "Cargo.toml").exists() and have("cargo"):
        add("cargo-check", ["cargo", "check", "--message-format=short"])
    if (root / "go.mod").exists() and have("go"):
        add("go-vet", ["go", "vet", "./..."])
    if any(root.glob(f) for f in ("*.sln", "*.csproj")) and have("dotnet"):
        add("dotnet-build", ["dotnet", "build", "--nologo", "-v", "q"])
    if not results:
        results.append(DiagnosticResult(
            tool="none", ok=True, output="",
            skipped_reason="No supported linter/compiler found on PATH; skipped diagnostics."))
    return results


# --- model context bundle ----------------------------------------------------

def build_model_context(*,
                        request_text: str,
                        error: ErrorInfo,
                        files: list[tuple[Path, str]],
                        diagnostics: list[DiagnosticResult],
                        local_summary: str,
                        budget_chars: int = 24_000) -> str:
    """Assemble a minimised, secrets-redacted evidence bundle for the model."""
    parts: list[str] = [f"USER REQUEST:\n{request_text[:2000]}"]
    if error.message:
        parts.append(f"ERROR MESSAGE:\n{error.message[:2000]}")
    if error.raw_excerpt:
        parts.append(f"ERROR EXCERPT (tail):\n{error.raw_excerpt[:3000]}")
    if local_summary:
        parts.append(f"LOCAL CONTEXT:\n{local_summary[:1500]}")
    diag_text = "\n\n".join(
        f"[{d.tool}]\n{(d.output or d.skipped_reason)[:2000]}" for d in diagnostics)
    if diag_text:
        parts.append(f"DIAGNOSTICS:\n{diag_text[:4000]}")
    used = sum(len(p) for p in parts)
    for path, content in files:
        if used >= budget_chars:
            parts.append("…[file budget exhausted; remaining files omitted]")
            break
        room = budget_chars - used
        snippet = content[:room - 500] if len(content) > room - 500 else content
        parts.append(f"FILE: {path}\n{snippet}")
        used += len(snippet) + 100
    bundle = "\n\n---\n\n".join(parts)
    return redact_secrets(bundle[:budget_chars])
