"""Code intelligence tools: search, outlines, tests, complexity.

These make Trace better at problem-solving and coding without ever running
untrusted project code by default:

- :func:`search_text` — bounded, exclude-aware grep over the project.
- :func:`file_outline` — classes/functions with line numbers (AST for Python,
  regex for JS/TS/Go/Rust/Java/C#).
- :func:`find_symbol` — locate a symbol definition across the project.
- :func:`detect_test_command` — allowlisted test runner for this project.
- :func:`run_project_tests` — runs ONLY the detected allowlisted command and
  ONLY when ``allowed=True`` (opt-in via config); truncated output.
- :func:`complexity_snapshot` — cheap LOC / long-function heuristics.

Stdlib only. Imports only ``project_inspector`` (excludes + safe reads).
Never imports the model, server, history, or UI code.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .project_inspector import EXCLUDE_DIRS, is_excluded, is_readable_name


@dataclass
class TextHit:
    path: str
    line: int
    snippet: str

    def to_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "snippet": self.snippet}


@dataclass
class OutlineEntry:
    kind: str  # "class" | "function" | "method"
    name: str
    line: int

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "line": self.line}


_FUNC_PATTERNS = [
    # JS/TS/Java/C#/Go/Rust/PHP/Swift-ish function + class shapes.
    re.compile(r"^\s*(?:export\s+|public\s+|private\s+|protected\s+|static\s+|async\s+)*"
               r"(?:function\s+(\w+)|class\s+(\w+)|interface\s+(\w+))"),
    re.compile(r"^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>"),
    re.compile(r"^\s*def\s+(\w+)\s*\("),   # py fallback (ast preferred)
    re.compile(r"^\s*class\s+(\w+)[\s(:]"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\("),  # go
    re.compile(r"^\s*fn\s+(\w+)\s*[\(<]"),  # rust
]


def search_text(root: Path, pattern: str,
                max_hits: int = 20) -> list[TextHit]:
    """Case-insensitive literal search over readable project files."""
    hits: list[TextHit] = []
    if root is None or not pattern or not pattern.strip():
        return hits
    try:
        root = root.resolve()
    except OSError:
        return hits
    needle = pattern.strip().lower()
    if len(needle) < 2:
        return hits
    try:
        rx = re.compile(re.escape(needle))
    except re.error:
        return hits
    count_files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        dirnames.sort()
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if is_excluded(rel) or not is_readable_name(p):
                continue
            try:
                if p.stat().st_size > 300_000:
                    continue
            except OSError:
                continue
            count_files += 1
            if count_files > 5_000:
                return hits
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line.lower()):
                    hits.append(TextHit(path=str(rel).replace("\\", "/"),
                                        line=i,
                                        snippet=line.strip()[:220]))
                    if len(hits) >= max_hits:
                        return hits
                if i > 3_000:
                    break
    return hits


def file_outline(path: Path, max_entries: int = 60) -> list[OutlineEntry]:
    """Outline of classes/functions in one file. Never raises."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    suffix = path.suffix.lower()
    if suffix in (".py", ".pyi"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            pass
        else:
            out: list[OutlineEntry] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    out.append(OutlineEntry("class", node.name, node.lineno))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(OutlineEntry("function", node.name, node.lineno))
            out.sort(key=lambda e: e.line)
            return out[:max_entries]
    out2: list[OutlineEntry] = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat in _FUNC_PATTERNS:
            m = pat.match(line)
            if m:
                name = next((g for g in m.groups() if g), None)
                if name:
                    kind = "class" if "class" in line and "def " not in line else "function"
                    out2.append(OutlineEntry(kind, name, i))
                break
        if len(out2) >= max_entries:
            break
    return out2


def find_symbol(root: Path, symbol: str,
                limit: int = 10) -> list[TextHit]:
    """Find likely definitions of ``symbol`` (def/class/func lines first)."""
    if not symbol or not symbol.strip():
        return []
    sym = symbol.strip()
    hits = search_text(root, sym, max_hits=limit * 4)
    defn = re.compile(r"\b(def|class|func|fn|function|interface)\s+" + re.escape(sym) + r"\b")
    ranked = sorted(hits,
                    key=lambda h: (0 if defn.search(h.snippet) else 1, h.path, h.line))
    return ranked[:limit]


def complexity_snapshot(path: Path, content: str | None = None,
                        long_fn_lines: int = 60) -> dict:
    """Cheap heuristics: LOC, function count, long-function notes. Never raises."""
    try:
        text = content if content is not None else path.read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return {"path": str(path), "loc": 0, "notes": ["unreadable"]}
    lines = text.splitlines()
    loc = len(lines)
    notes: list[str] = []
    try:
        if path.suffix.lower() in (".py", ".pyi"):
            tree = ast.parse(text)
            funcs = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            long = [f.name for f in funcs
                    if (getattr(f, "end_lineno", f.lineno) or f.lineno) - f.lineno > long_fn_lines]
            if long:
                notes.append(f"long functions (>{long_fn_lines} lines): {', '.join(long[:5])}")
            if len(funcs) > 25:
                notes.append(f"many functions ({len(funcs)}): consider splitting")
            return {"path": str(path), "loc": loc,
                    "functions": len(funcs), "notes": notes}
    except Exception:
        pass
    if loc > 800:
        notes.append(f"large file ({loc} lines): prefer targeted excerpts")
    return {"path": str(path), "loc": loc, "notes": notes}


def detect_test_command(root: Path) -> list[str] | None:
    """Allowlisted test command for this project, or None if unknown.

    Only well-known runners, never project scripts. Callers must still gate
    execution behind an explicit opt-in flag.
    """
    if root is None:
        return None
    try:
        if (root / "pytest.ini").exists() or (root / "tests").is_dir() \
                or (root / "test").is_dir() or list(root.glob("test_*.py")) \
                or list(root.glob("*_test.py")):
            if shutil.which("pytest") or shutil.which("python"):
                return ["python", "-m", "pytest", "-q"]
        if shutil.which("python"):
            return ["python", "-m", "unittest"]
    except OSError:
        pass
    try:
        if (root / "package.json").exists() and shutil.which("npm"):
            return ["npm", "test", "--", "--watchAll=false"]
        if (root / "Cargo.toml").exists() and shutil.which("cargo"):
            return ["cargo", "test"]
        if (root / "go.mod").exists() and shutil.which("go"):
            return ["go", "test", "./..."]
        if (next(root.glob("*.sln"), None) or next(root.glob("*.csproj"), None)) \
                and shutil.which("dotnet"):
            return ["dotnet", "test", "--nologo"]
    except OSError:
        pass
    return None


def run_project_tests(root: Path, timeout_s: float = 120.0,
                      *, allowed: bool = False) -> dict:
    """Run the detected allowlisted test command. Opt-in only.

    Returns ``{"ran": bool, "command": [...], "output": str, "reason": str}``.
    Never raises, never runs project scripts directly, always truncates.
    """
    if not allowed:
        return {"ran": False, "command": [], "output": "",
                "reason": "test execution is disabled (opt-in only)"}
    cmd = detect_test_command(root)
    if not cmd:
        return {"ran": False, "command": [], "output": "",
                "reason": "no supported test runner found"}
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True,
                              text=True, timeout=timeout_s)
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return {"ran": True, "command": cmd,
                "output": out[-6000:],
                "reason": f"exit={proc.returncode}"}
    except subprocess.TimeoutExpired:
        return {"ran": True, "command": cmd, "output": "",
                "reason": f"timed out after {timeout_s}s"}
    except Exception as e:
        return {"ran": False, "command": cmd or [], "output": "",
                "reason": f"failed: {e}"}
