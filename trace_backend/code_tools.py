"""Code intelligence tools: search, outlines, tests, complexity, structure.

These make Trace better at problem-solving and coding without ever running
untrusted project code by default:

- :func:`search_text` — bounded, exclude-aware grep over the project.
- :func:`search_regex` — the same grep with a real regex pattern.
- :func:`search_many` / :func:`find_symbols` — several searches or symbol
  lookups run concurrently on a bounded thread pool (walks are expensive).
- :func:`file_outline` — classes/functions with line numbers (AST for Python,
  regex for JS/TS/Go/Rust/Java/C#).
- :func:`find_symbol` — locate a symbol definition across the project.
- :func:`recent_files` — most-recently-modified readable files (recency truth
  when nothing matched the query).
- :func:`project_tree` — bounded directory overview so the model can orient.
- :func:`parse_test_failures` — turn test-runner output into file:line anchors.
- :func:`check_json_config` — syntax-check top-level *.json config files.
- :func:`scan_annotations` — TODO/FIXME/XXX/HACK anchors in already-read files.
- :func:`detect_test_command` — allowlisted test runner for this project.
- :func:`run_project_tests` — runs ONLY the detected allowlisted command and
  ONLY when ``allowed=True`` (opt-in via config); truncated output.
- :func:`complexity_snapshot` — cheap LOC / long-function heuristics.

Stdlib only. Imports only ``project_inspector`` (excludes + safe reads).
Never imports the model, server, history, or UI code. Every parallel helper
uses a bounded thread pool and a sequential fallback, and each function is
exception-safe: a tool failing must never break an investigation.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
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
                max_hits: int = 20, *, regex: bool = False) -> list[TextHit]:
    """Case-insensitive literal search over readable project files.

    With ``regex=True`` the pattern is compiled as-is (also case-insensitive);
    a bad or oversized regex simply yields no hits instead of raising.
    """
    hits: list[TextHit] = []
    if root is None or not pattern or not pattern.strip():
        return hits
    try:
        root = root.resolve()
    except OSError:
        return hits
    needle = pattern.strip()
    if len(needle) < 2 or len(needle) > 200:
        return hits
    try:
        if regex:
            rx = re.compile(needle, re.IGNORECASE)
        else:
            rx = re.compile(re.escape(needle.lower()))
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
                try:
                    found = rx.search(line) if regex else rx.search(line.lower())
                except re.error:
                    break
                if found:
                    hits.append(TextHit(path=str(rel).replace("\\", "/"),
                                        line=i,
                                        snippet=line.strip()[:220]))
                    if len(hits) >= max_hits:
                        return hits
                if i > 3_000:
                    break
    return hits


# --- bounded-parallelism helpers --------------------------------------------
# Each expensive tool job (a tree walk, a file parse) is a plain Python call.
# Running a handful of them concurrently on a small thread pool is the cheap
# way to make collection faster without restructuring the pipeline.

def _pool_workers(n_tasks: int) -> int:
    """A small bounded worker count for helper-side I/O parallelism."""
    return max(1, min(8, n_tasks, (os.cpu_count() or 4) + 2))


def _run_map(fn, items: list,
             fallback_err=None):
    """Run ``fn`` over ``items`` concurrently, preserving order. Never raises.

    A task that raises yields ``fallback_err(item)`` (or ``None``) in its
    slot; if the pool itself cannot run, falls back to a sequential map.
    """
    if not items:
        return []
    if len(items) == 1:
        try:
            return [fn(items[0])]
        except Exception:
            return [None if fallback_err is None else fallback_err(items[0])]

    def guarded(item):
        try:
            return fn(item)
        except Exception:
            return None if fallback_err is None else fallback_err(item)

    try:
        with ThreadPoolExecutor(max_workers=_pool_workers(len(items)),
                                thread_name_prefix="trace-tools") as ex:
            return list(ex.map(guarded, items))
    except Exception:
        return [guarded(item) for item in items]


def search_regex(root: Path, pattern: str,
                 max_hits: int = 20) -> list[TextHit]:
    """Regex search over the project; bad patterns yield nothing, not errors."""
    return search_text(root, pattern, max_hits=max_hits, regex=True)


def search_many(root: Path, patterns: list[str],
                max_hits_each: int = 8) -> dict[str, list[TextHit]]:
    """Several concurrent searches; dict keyed by pattern (input order).

    Duplicates are collapsed, short/empty patterns dropped, and the whole
    result is bounded by ``max_hits_each`` — enough to anchor evidence, not
    enough to flood the bundle.
    """
    wanted = [p.strip() for p in (patterns or []) if p and len(p.strip()) >= 2
              and len(p.strip()) <= 200]
    ordered = list(dict.fromkeys(wanted))[:6]
    if not ordered:
        return {}
    if len(ordered) == 1:
        return {ordered[0]: search_text(root, ordered[0], max_hits=max_hits_each)}
    results: dict[str, list[TextHit]] = {}
    mapped = _run_map(lambda p: search_text(root, p, max_hits=max_hits_each),
                      ordered)
    for pat, hits in zip(ordered, mapped):
        results[pat] = list(hits or [])
    return results


def find_symbols(root: Path, symbols: list[str],
                 limit: int = 6) -> dict[str, list[TextHit]]:
    """Locate several symbols concurrently (definition-ranked per symbol)."""
    wanted = [s.strip() for s in (symbols or []) if s and s.strip()
              and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{1,64}", s.strip())]
    ordered = list(dict.fromkeys(wanted))[:6]
    if not ordered:
        return {}
    if len(ordered) == 1:
        return {ordered[0]: find_symbol(root, ordered[0], limit=limit)}
    mapped = _run_map(lambda s: find_symbol(root, s, limit=limit), ordered)
    out: dict[str, list[TextHit]] = {}
    for sym, hits in zip(ordered, mapped):
        out[sym] = list(hits or [])
    return out


def recent_files(root: Path, limit: int = 10) -> list[dict]:
    """Readable project files most recently modified (bounded walk). Recency
    is honest disk truth — useful when the query matched nothing. Each entry:
    ``{"path": rel, "minutes_ago": int, "loc_cap": size_guard}``."""
    out: list[tuple[float, str]] = []
    try:
        root = root.resolve()
    except OSError:
        return []
    count = 0
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
                mtime = p.stat().st_mtime
            except OSError:
                continue
            out.append((mtime, str(rel).replace("\\", "/")))
            count += 1
            if count > 20_000:
                break
        if count > 20_000:
            break
    out.sort(reverse=True)
    now = time.time()
    return [{"path": rel, "minutes_ago": max(0, int((now - mt) // 60))}
            for mt, rel in out[: max(0, limit)]]


def project_tree(root: Path, max_depth: int = 2, max_entries: int = 80,
                 max_chars: int = 2400) -> str:
    """Bounded directory overview: readable files only, deps excluded.

    A map of *what the verifier could read on disk* — never a promise that
    more code does not exist. Deterministic ordering, hard caps.
    """
    try:
        root = root.resolve()
    except OSError:
        return ""
    lines: list[str] = [f"{root.name or root}/"]
    entries = 0

    def walk(dirpath: Path, prefix: str, depth: int) -> None:
        nonlocal entries
        if depth < 0 or entries >= max_entries:
            return
        try:
            dirs = sorted(d for d in os.scandir(dirpath) if d.is_dir())
            files = sorted((f for f in os.scandir(dirpath) if f.is_file()),
                           key=lambda f: f.name)
        except OSError:
            return
        for d in dirs:
            if entries >= max_entries:
                lines.append(prefix + "…")
                return
            if d.name in EXCLUDE_DIRS:
                continue
            lines.append(f"{prefix}{d.name}/")
            entries += 1
            walk(Path(d.path), prefix + "  ", depth - 1)
        for f in files:
            if entries >= max_entries:
                lines.append(prefix + "…")
                return
            fp = Path(f.path)
            if not is_readable_name(fp) or is_excluded(fp.relative_to(root)
                                                       if fp != root else Path(".")):
                continue
            lines.append(f"{prefix}{f.name}")
            entries += 1

    walk(root, "  ", max_depth)
    if entries >= max_entries:
        lines.append(f"…[{entries} entries shown, tree truncated]")
    return "\n".join(lines)[:max_chars]


_TEST_FAIL_PATTERNS = [
    # pytest short summary: "FAILED tests/test_x.py::Klass::test_y - AssertionError"
    re.compile(r"(?m)^\s*FAILED\s+([\w/.\-]+\.[A-Za-z]+)(?::(\d+))?"),
    re.compile(r'(?m)^\s*File "([^"]+)", line (\d+)'),
    re.compile(r"(?m)^\s*([\w/.\-]+\.(?:py|ts|tsx|js|mjs|cjs|go|rs)):(\d+):"),
    re.compile(r"(?m)^\s*([\w/.\-]+\.(?:ts|tsx|js|mjs|cjs)):(\d+):\d+"),
    # unittest: "FAIL: test_x (tests.test_y.Klass)" → tests/test_y.py
    re.compile(r"(?m)^\s*(?:FAIL|ERROR):\s+\S+\s+\((\w[\w.]*)\.(\w+)\)"),
]


def parse_test_failures(output: str, limit: int = 10) -> list[TextHit]:
    """Extract file:line anchors from allowlisted test-runner output.

    Everything here comes from the runner's own text — the same parent tool
    that would print it in your terminal — so the anchors are grounded, and
    the validator still re-checks anything the model cites from them.
    """
    hits: list[TextHit] = []
    if not output or not output.strip():
        return hits
    seen: set[tuple[str, int]] = set()
    for pat in _TEST_FAIL_PATTERNS:
        for m in pat.finditer(output[:16_000]):
            if pat is _TEST_FAIL_PATTERNS[-1]:
                # (module.Class) form: map "tests.test_y" → "tests/test_y.py".
                path = m.group(1).replace(".", "/") + ".py"
                line = 1
            else:
                path = m.group(1).replace("\\", "/")
                line = int(m.group(2) or 1)
            key = (path, line)
            if key in seen:
                continue
            seen.add(key)
            hits.append(TextHit(path=path, line=line, snippet="test failure"))
            if len(hits) >= max(1, limit):
                return hits
    return hits


def check_json_config(root: Path, limit: int = 15) -> list[str]:
    """Syntax-check top-level ``*.json`` config files with the stdlib parser.

    A broken package.json/tsconfig.json often *is* the bug; this surfaces it
    verbatim. Empty return means every checked file parsed — also an honest,
    useful fact.
    """
    notes: list[str] = []
    if root is None:
        return notes
    try:
        candidates = sorted(root.glob("*.json"))[:limit]
    except OSError:
        return notes
    for p in candidates:
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        try:
            if p.stat().st_size > 200_000:
                notes.append(f"{rel}: too large to check (>200 KB)")
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            notes.append(f"{rel}: unreadable ({type(e).__name__})")
            continue
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            notes.append(f"{rel}: invalid JSON — {e}")
        except Exception:
            notes.append(f"{rel}: could not parse")
    return notes


_ANNOTATION_RX = re.compile(r"(?i)\b(TODO|FIXME|XXX|HACK)\b")


def scan_annotations(items: list[tuple[str, str]],
                     max_notes: int = 20) -> list[dict]:
    """TODO/FIXME/XXX/HACK anchors inside already-read file contents.

    Takes ``(relpath, content)`` pairs — no disk access of its own — so it
    can only ever report what the pipeline actually read.
    """
    out: list[dict] = []
    for rel, content in items or []:
        if not content:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if _ANNOTATION_RX.search(line):
                out.append({"path": str(rel), "line": i,
                            "note": line.strip()[:160]})
                if len(out) >= max(1, max_notes):
                    return out
    return out


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
