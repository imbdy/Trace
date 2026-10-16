"""Pure view-model for investigation results (no tkinter).

Transforms the backend's InvestigationStatus JSON into display-ready
structures. Honesty rules enforced here, not in the renderer:
- verified findings and unverified hypotheses are kept distinct;
- missing excerpts/diffs are reported as missing, never synthesized;
- incomplete investigations (error/cancelled/running) produce an explicit
  status banner instead of partial-looking results.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FindingView:
    title: str
    file_path: str
    start_line: int
    end_line: int
    excerpt: str
    explanation: str
    evidence: str
    confidence: str
    verified: bool
    location_label: str = ""
    verification_note: str = ""
    # Grounded version-control context ("last changed in commit …") or "".
    git_context: str = ""


@dataclass
class ResultView:
    complete: bool
    status: str
    banner: str = ""
    investigation_id: str = ""
    summary: str = ""
    root_cause: str = ""
    confidence: str = "low"
    confidence_rationale: str = ""
    findings: list[FindingView] = field(default_factory=list)
    verified_findings: list[FindingView] = field(default_factory=list)
    hypotheses: list[FindingView] = field(default_factory=list)
    fix_description: str = ""
    fix_diff: str = ""
    fix_rationale: str = ""
    # True only when the fix's diff was verified against the real file; a fix
    # shown with this False must not be treated as applicable.
    fix_diff_verified: bool = False
    # Populated when auto_fix wrote the diff to disk (files + .trace-bak).
    fix_applied: dict | None = None
    # "debug" (default) or "explain": echoed from the request so the results
    # window can tailor the verdict view (e.g. no fix/apply sections).
    investigation_type: str = "debug"
    alternative_hypotheses: list[str] = field(default_factory=list)
    needs_more_info: list[str] = field(default_factory=list)
    evidence_summary: str = ""
    warnings: list[str] = field(default_factory=list)


def _s(value, limit: int = 6000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "\n…[truncated]"


def format_location(file_path: str, start: int, end: int) -> str:
    if not file_path:
        return "(no file reported)"
    if start <= 0:
        return file_path
    if end <= start:
        return f"{file_path}:{start}"
    return f"{file_path}:{start}–{end}"


def build_result_view(status: dict) -> ResultView:
    """Build a ResultView from InvestigationStatus JSON. Never raises."""
    try:
        return _build(status if isinstance(status, dict) else {})
    except Exception as e:  # renderer must never crash on odd payloads
        return ResultView(complete=False, status="error",
                          banner=f"Could not interpret backend response: {e}")


def _build(status: dict) -> ResultView:
    state = str(status.get("status", ""))
    if state == "cancelled":
        return ResultView(complete=False, status=state,
                          banner="Investigation cancelled — no results. "
                                 "Submit a new request when ready.")
    if state == "running":
        stage = str(status.get("stage", ""))
        return ResultView(complete=False, status=state,
                          banner=f"Investigation still running (stage: {stage or '?'}). "
                                 "Results will appear when it finishes.")
    if state == "error":
        err = status.get("error") or {}
        msg = err.get("message", "Unknown backend error") \
            if isinstance(err, dict) else str(err)
        return ResultView(complete=False, status=state,
                          banner=f"Investigation failed: {msg}")
    report = status.get("report")
    if state != "done" or not isinstance(report, dict):
        return ResultView(complete=False, status=state or "unknown",
                          banner="No report available for this investigation.")

    findings: list[FindingView] = []
    for f in report.get("findings", []) or []:
        if not isinstance(f, dict):
            continue
        try:
            start = int(f.get("start_line", 0) or 0)
            end = int(f.get("end_line", 0) or start)
        except (ValueError, TypeError):
            start, end = 0, 0
        verified = bool(f.get("verified"))
        excerpt = _s(f.get("code_excerpt", ""), 4000)
        fv = FindingView(
            title=_s(f.get("title", "Finding"), 300),
            file_path=str(f.get("file_path", "")),
            start_line=start, end_line=end,
            excerpt=excerpt,
            explanation=_s(f.get("explanation", "")),
            evidence=_s(f.get("evidence", "")),
            confidence=str(f.get("confidence", "low")),
            verified=verified,
            location_label=format_location(str(f.get("file_path", "")), start, end),
            git_context=_s(f.get("git_context", "")),
            verification_note=(
                "Verified against files on disk."
                if verified else
                "UNVERIFIED — treat as hypothesis, not fact. "
                + ("No code excerpt was returned for this location."
                   if not excerpt else "Location could not be confirmed on disk.")),
        )
        findings.append(fv)

    rec = report.get("recommended_fix") or {}
    if not isinstance(rec, dict):
        rec = {}

    fix_applied = report.get("fix_applied")
    return ResultView(
        complete=True,
        status="done",
        investigation_id=str(report.get("investigation_id", "")
                              or status.get("investigation_id", "")),
        summary=_s(report.get("summary", "")) or "No summary provided.",
        root_cause=_s(report.get("root_cause", "")) or "Unknown.",
        confidence=str(report.get("confidence", "low")),
        confidence_rationale=_s(report.get("confidence_rationale", ""), 2000),
        findings=findings,
        verified_findings=[f for f in findings if f.verified],
        hypotheses=[f for f in findings if not f.verified],
        fix_description=_s(rec.get("description", "")),
        fix_diff=_s(rec.get("diff", ""), 8000),
        fix_rationale=_s(rec.get("rationale", ""), 2000),
        fix_diff_verified=bool(rec.get("diff_verified", False)),
        fix_applied=fix_applied if isinstance(fix_applied, dict) else None,
        investigation_type=str(status.get("investigation_type", "debug")
                               or "debug"),
        alternative_hypotheses=[_s(h, 2000) for h in
                                (report.get("alternative_hypotheses", []) or [])][:10],
        needs_more_info=[_s(n, 1000) for n in
                         (report.get("needs_more_info", []) or [])][:10],
        evidence_summary=_s(report.get("evidence_summary", "")),
        warnings=[_s(w, 1000) for w in (report.get("warnings", []) or [])],
    )
