"""Verification: every file path / line number in a report must exist on disk."""

from __future__ import annotations

from pathlib import Path

from .project_inspector import resolve_within_root
from .schemas import Finding, InvestigationReport


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


def sanitize_report(root: Path | None, report: InvestigationReport) -> InvestigationReport:
    """Verify every finding; label (never invent) unverified references."""
    warnings = list(report.warnings)
    for f in report.findings:
        before = f.verified
        verify_finding(root, f)
        if not f.verified and before is False:
            warnings.append(f"Unverified location: {f.file_path}:{f.start_line}-{f.end_line}")
    # Deduplicate warnings preserving order.
    seen: set[str] = set()
    report.warnings = [w for w in warnings if not (w in seen or seen.add(w))]
    if report.confidence not in ("low", "medium", "high"):
        report.confidence = "low"
        report.warnings.append("Model returned invalid confidence; downgraded to low.")
    return report
