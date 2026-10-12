"""Verification: every file path / line number in a report must exist on disk."""

from __future__ import annotations

import re
from pathlib import Path

from .project_inspector import resolve_within_root
from .schemas import Finding, InvestigationReport, RecommendedFix


def read_lines(path: Path, start: int, end: int, context: int = 0) -> tuple[str, int]:
    """Return (excerpt, total_lines). 1-indexed inclusive range + context."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", 0
    lines = text.splitlines()
    total = len(lines)
    s = max(1, start - context)
    e = min(total, end + context)
    if s > e or total == 0:
        return "", total
    excerpt = "\n".join(f"{i + 1:>5}: {lines[i]}" for i in range(s - 1, e))
    return excerpt, total


def verify_location(root: Path | None, file_path: str,
                    start_line: int, end_line: int) -> dict:
    """Check a single location. Never raises; returns a verdict dict."""
    if not file_path or not file_path.strip():
        return {"exists": False, "line_valid": False, "total_lines": 0,
                "excerpt": "", "warning": "empty file path"}
    p = Path(file_path)
    resolved: Path | None = None
    if root is not None:
        resolved = resolve_within_root(root, file_path)
        if resolved is None and p.is_absolute() and p.exists():
            # Absolute path outside root: still report honestly (unverified).
            resolved = p
        if resolved is None:
            return {"exists": False, "line_valid": False, "total_lines": 0,
                    "excerpt": "",
                    "warning": f"path escapes project root: {file_path}"}
    else:
        resolved = p
    if not resolved.exists() or not resolved.is_file():
        return {"exists": False, "line_valid": False, "total_lines": 0,
                "excerpt": "", "warning": f"file not found: {file_path}"}
    if start_line < 1 or end_line < start_line:
        return {"exists": True, "line_valid": False, "total_lines": 0,
                "excerpt": "", "warning": f"invalid line range {start_line}-{end_line}"}
    excerpt, total = read_lines(resolved, start_line, end_line)
    if start_line > total or end_line > total:
        return {"exists": True, "line_valid": False, "total_lines": total,
                "excerpt": excerpt,
                "warning": f"lines {start_line}-{end_line} exceed file length ({total})"}
    return {"exists": True, "line_valid": True, "total_lines": total,
            "excerpt": excerpt, "warning": "",
            "resolved": str(resolved)}


def verify_finding(root: Path | None, finding: Finding) -> Finding:
    verdict = verify_location(root, finding.file_path,
                              finding.start_line, finding.end_line)
    finding.verified = bool(verdict["exists"] and verdict["line_valid"])
    if finding.verified:
        if not finding.code_excerpt:
            finding.code_excerpt = verdict["excerpt"]
    else:
        note = verdict.get("warning", "unverified")
        finding.evidence = ((finding.evidence + "\n" if finding.evidence else "")
                            + f"[UNVERIFIED: {note}]")
    return finding


# --- recommended-fix diff verification --------------------------------------
# A model's ``diff`` is currently shown as if it applied to the real file. To
# keep the honesty contract, each unified-diff hunk is re-grounded against the
# file on disk: the "old" side (context + removed lines) must match the real
# file starting at the hunk's old line. Added lines are the proposed change and
# are deliberately NOT matched (they are not in the file yet). Never raises.

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _diff_path(line: str) -> str:
    """Extract the path from a `+++ b/path` / `--- a/path` diff header."""
    p = line[4:].strip() if len(line) >= 4 else ""
    p = p.split("\t")[0].strip()  # drop a trailing git timestamp
    for pre in ("a/", "b/"):
        if p.startswith(pre):
            p = p[len(pre):]
    return p


def _parse_diff_hunks(diff: str) -> list[dict]:
    """Parse a unified diff into per-hunk dicts.

    Each dict has: path, old_start, new_start, old (context+removed lines) and
    new (context+added lines), all stripped of their +/-/space markers. Tolerant
    of blank context lines and the `\\ No newline at end of file` marker.
    """
    hunks: list[dict] = []
    cur: dict | None = None
    path = ""

    def flush() -> None:
        nonlocal cur
        if cur is not None and cur.get("path"):
            hunks.append(cur)
        cur = None

    for raw in diff.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("+++ "):
            flush()
            path = _diff_path(line)
            continue
        if line.startswith("--- "):
            continue
        m = _HUNK_RE.match(line)
        if m:
            flush()
            cur = {"path": path, "old_start": int(m.group(1)),
                   "new_start": int(m.group(2)), "old": [], "new": []}
            continue
        if cur is None:
            continue
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        marker, content = line[:1], line[1:]
        if marker == "+":
            cur["new"].append(content)
        elif marker == "-":
            cur["old"].append(content)
        elif marker == " " or line == "":
            body = content if marker == " " else ""
            cur["old"].append(body)
            cur["new"].append(body)
        else:
            flush()  # foreign line ends the hunk
    flush()
    return hunks


def _old_side_matches(root: Path | None, path: str, start: int,
                      old_lines: list[str]) -> bool:
    """True when ``old_lines`` equal the real file starting at 1-indexed ``start``."""
    p = resolve_within_root(root, path) if root is not None else Path(path)
    if p is None:
        return False
    try:
        real = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    seg = real[start - 1: start - 1 + len(old_lines)]
    if len(seg) < len(old_lines):
        return False
    return [x.rstrip() for x in seg] == [x.rstrip() for x in old_lines]


def _diff_warning(path: str, line: int) -> str:
    return (f"The suggested fix could not be verified against {path}:{line} "
            f"and may be inaccurate.")


def verify_fix_diff(root: Path | None,
                    fix: RecommendedFix | None) -> tuple[bool, list[str]]:
    """Best-effort verification of a fix's diff against disk.

    Returns ``(diff_verified, warnings)``. A missing/empty/unparseable diff is
    left unverified with NO warning (nothing fabricated was claimed). A hunk
    whose path is missing or escapes the root, whose line is out of range, or
    whose old-side content does not match the real file makes the whole diff
    unverified and appends a clear warning. Never raises.
    """
    if fix is None or not getattr(fix, "diff", None) or not fix.diff.strip():
        return False, []
    try:
        hunks = _parse_diff_hunks(fix.diff)
    except Exception:  # pragma: no cover - parser is defensive
        return False, []
    if not hunks:
        return False, []
    warnings: list[str] = []
    ok = True
    for h in hunks:
        path = h["path"]
        if not path or path == "/dev/null":
            continue  # new-file / deleted-file headers: nothing to ground against
        start = h["old_start"]
        verdict = verify_location(root, path, start, start)
        if not (verdict.get("exists") and verdict.get("line_valid")):
            ok = False
            warnings.append(_diff_warning(path, start))
            continue
        if h["old"] and not _old_side_matches(root, path, start, h["old"]):
            ok = False
            warnings.append(_diff_warning(path, start))
    return ok, warnings


def sanitize_report(root: Path | None, report: InvestigationReport) -> InvestigationReport:
    """Verify every finding; label (never invent) unverified references."""
    warnings = list(report.warnings)
    for f in report.findings:
        before = f.verified
        verify_finding(root, f)
        if not f.verified and before is False:
            warnings.append(f"Unverified location: {f.file_path}:{f.start_line}-{f.end_line}")
    # The recommended fix's diff is verified against disk too: if it cannot be
    # grounded in the real file it is labelled, not presented as applicable.
    if report.recommended_fix is not None:
        fix = report.recommended_fix
        fix.diff_verified, fix_warnings = verify_fix_diff(root, fix)
        warnings.extend(fix_warnings)
    # Deduplicate warnings preserving order.
    seen: set[str] = set()
    report.warnings = [w for w in warnings if not (w in seen or seen.add(w))]
    if report.confidence not in ("low", "medium", "high"):
        report.confidence = "low"
        report.warnings.append("Model returned invalid confidence; downgraded to low.")
    return report
