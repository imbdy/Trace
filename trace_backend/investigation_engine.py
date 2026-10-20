"""Investigation engine: the 10-stage pipeline.

Stages: receive → screenshot → context → project_root → error_extract →
read_files → diagnostics → model_analysis → validate → report.

- Combines screenshot + code + diagnostics; a screenshot alone is never
  treated as sufficient evidence.
- Never invents paths/line numbers: all model-proposed locations are
  verified against disk (validator.sanitize_report).
- Supports cancellation via threading.Event at every stage + during streaming.
- Malformed model output degrades to a low-confidence honest report, never
  a fabricated one.
- Independent collection work runs concurrently on small, bounded thread
  pools (screenshot ∥ local context; parallel file reads; diagnostics ∥
  tests ∥ code intelligence; per-finding git enrichment). Every parallel
  helper degrades to a sequential fallback on failure, and every result is
  still disk-verified downstream — parallelism changes speed, never rules.
- ``follow_up`` answers a question about an existing verdict: the prior
  report is the context (re-read from disk where it can be), and the answer
  passes through the same validator as a first-pass report.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent_graph import END, Graph
from .code_fixer import apply_unified_diff
from .code_tools import (
    complexity_snapshot,
    check_json_config,
    file_outline,
    find_symbols,
    parse_test_failures,
    project_tree,
    recent_files,
    run_project_tests,
    scan_annotations,
    search_many,
)
from .config import TraceConfig
from .context_collector import LocalContext, collect_local_context
from .git_context import finding_git_context
from .ollama_client import OllamaCancelledError, OllamaClient, OllamaError
from .project_inspector import (
    ErrorInfo,
    build_model_context,
    collect_diagnostics,
    extract_error_info,
    find_project_root,
    follow_imports,
    list_candidate_files,
    redact_secrets,
    resolve_within_root,
    safe_read,
)
from .schemas import (
    STAGES,
    Finding,
    InvestigationReport,
    InvestigationRequest,
    ProgressEvent,
    RecommendedFix,
    new_investigation_id,
)
from .screenshot_service import (
    ScreenshotCaptureError,
    ScreenshotResult,
    capture_screenshot,
    dispose,
)
from .validator import sanitize_report


class InvestigationCancelled(Exception):
    pass


ProgressCallback = Callable[[ProgressEvent], None]


SYSTEM_PROMPT = """You are Trace, a careful debugging assistant. Analyze the provided evidence \
(screenshots described as text, code excerpts, outlines, error messages, diagnostics, \
test output) and return ONLY valid JSON with this exact schema:
{"summary": str, "root_cause": str, "confidence": "low"|"medium"|"high",
 "confidence_rationale": str, "findings": [{"title": str, "file_path": str, \
"start_line": int, "end_line": int, "explanation": str, "evidence": str}],
 "recommended_fix": {"description": str, "diff": str, "rationale": str},
 "alternative_hypotheses": [str], "needs_more_info": [str], "evidence_summary": str}
Rules: only cite file paths and line numbers that appear in the provided FILE excerpts, \
error stack frames, SEARCH HITS, test failure anchors, or their outlines. The PROJECT TREE / \
RECENTLY MODIFIED / CONFIG CHECKS sections list what was actually read from disk — cite those \
paths the same way when the bug involves file naming or configuration. Never invent paths, line \
numbers, compiler output, or test results. If evidence is insufficient, set confidence to "low", \
explain what is known vs uncertain, and list the next \
diagnostic step in needs_more_info. When you propose a fix, return a MINIMAL unified diff \
("--- a/path" / "+++ b/path" with @@ hunks) whose context lines match the real file exactly, \
so it can be applied directly; keep hunks small and scoped to the bug."""


EXPLAIN_SYSTEM_PROMPT = """You are Trace, a careful code-explanation assistant. Analyze the provided code
excerpts and return ONLY valid JSON with this exact schema:
{"summary": str, "root_cause": str, "confidence": "low"|"medium"|"high",
 "confidence_rationale": str, "findings": [{"title": str, "file_path": str,
 "start_line": int, "end_line": int, "explanation": str, "evidence": str}],
 "recommended_fix": {"description": str, "diff": str, "rationale": str},
 "alternative_hypotheses": [str], "needs_more_info": [str], "evidence_summary": str}
Rules: Explain what the code does, how it works, and any notable behavior or
potential issues. Only cite file paths and line numbers that appear in the
provided FILE excerpts. Never invent paths, line numbers, or behavior. If
evidence is insufficient, set confidence to "low" and explain what is known
vs uncertain. The "root_cause" field should describe the purpose/intent of
the code (not a bug). Do not propose fixes unless there is a clear bug."""


def _check_cancelled(cancel: threading.Event | None):
    if cancel is not None and cancel.is_set():
        raise InvestigationCancelled("Investigation cancelled by user.")


FOLLOW_UP_SYSTEM_PROMPT = """You are Trace, a careful debugging assistant continuing an earlier \
investigation. You receive the PRIOR REPORT (produced earlier; findings marked verified=true were \
checked against the real files), a FOLLOW-UP QUESTION, and — when available — those same files \
re-read from disk with numbered lines. Return ONLY valid JSON with this exact schema:
{"summary": str, "root_cause": str, "confidence": "low"|"medium"|"high",
  "confidence_rationale": str, "findings": [{"title": str, "file_path": str,
  "start_line": int, "end_line": int, "explanation": str, "evidence": str}],
  "recommended_fix": {"description": str, "diff": str, "rationale": str},
  "alternative_hypotheses": [str], "needs_more_info": [str], "evidence_summary": str}
Rules: answer the FOLLOW-UP QUESTION, not the original request. Only cite file paths and line \
numbers that appear in the numbered FILE excerpts or in prior findings marked verified=true. Never \
invent paths, line numbers, compiler output, or test results. If the question cannot be answered \
from the evidence, set confidence to "low", separate what is known from what is uncertain, and put \
the next diagnostic step in needs_more_info."""


def _prior_as_dict(prior: InvestigationReport | dict | None) -> dict:
    """Normalise a prior report (live object or recalled JSON dict) to a dict."""
    if prior is None:
        return {}
    if isinstance(prior, dict):
        return prior
    return prior.to_dict()


def _prior_report_text(prior: dict) -> str:
    """Render a prior verdict as model-readable context, or say it is missing."""
    if not prior:
        return ("PRIOR REPORT: unavailable — no prior verdict was supplied. Answer "
                "only from the FOLLOW-UP QUESTION and any FILE excerpts below; if "
                "that is not enough, say so with confidence \"low\".")
    lines = [
        f"PRIOR REPORT (id={prior.get('investigation_id', '')}, "
        f"confidence={prior.get('confidence', '')}):",
        f"summary: {prior.get('summary', '')}",
        f"root_cause: {prior.get('root_cause', '')}",
    ]
    if prior.get("confidence_rationale"):
        lines.append(f"confidence_rationale: {prior['confidence_rationale']}")
    fix = prior.get("recommended_fix")
    if isinstance(fix, dict) and (fix.get("description") or fix.get("diff")):
        lines.append(f"recommended_fix: {fix.get('description', '')}")
    for h in prior.get("alternative_hypotheses") or []:
        lines.append(f"alternative_hypothesis: {h}")
    for f in prior.get("findings") or []:
        if not isinstance(f, dict):
            continue
        lines.append(
            f"finding: {f.get('title', '')} @ {f.get('file_path', '')}:"
            f"{f.get('start_line', '')}-{f.get('end_line', '')} "
            f"verified={bool(f.get('verified'))}: {f.get('explanation', '')}")
    for n in prior.get("needs_more_info") or []:
        lines.append(f"needs_more_info: {n}")
    for w in prior.get("warnings") or []:
        lines.append(f"warning: {w}")
    return "\n".join(lines)[:8000]


def _numbered(content: str, start: int, end: int) -> str:
    """A line-numbered slice of ``content`` so the model can cite real lines."""
    lines = content.splitlines()
    s, e = max(1, start), min(len(lines), end)
    if s > e:
        return ""
    return "\n".join(f"{i:>5}: {lines[i - 1]}" for i in range(s, e + 1))


def _enrich_git_context(root: Path | None, report: InvestigationReport) -> None:
    """Attach grounded "last changed in …" VC context to verified findings.

    Best-effort enrichment (see git_context): only verified findings get a
    value, and any git failure simply leaves ``git_context`` empty — honest,
    never fabricated. Each finding's lookup is an independent git subprocess,
    so verified findings are enriched concurrently (order preserved).
    """
    if root is None:
        return
    verified = [(i, f) for i, f in enumerate(report.findings) if f.verified]
    if not verified:
        return

    def one(item):
        idx, f = item
        try:
            return idx, finding_git_context(root, f.file_path, f.start_line)
        except Exception:  # pragma: no cover - git_context is already defensive
            return idx, ""

    try:
        with ThreadPoolExecutor(
                max_workers=max(2, min(6, len(verified))),
                thread_name_prefix="trace-git") as ex:
            for idx, ctx in list(ex.map(one, verified)):
                report.findings[idx].git_context = ctx
    except Exception:  # sequential fallback: same behaviour, slower
        for idx, f in verified:
            try:
                report.findings[idx].git_context = finding_git_context(
                    root, f.file_path, f.start_line)
            except Exception:
                report.findings[idx].git_context = ""


def _is_within(path: Path, root: Path) -> bool:
    """True when ``path`` resolves inside ``root`` (shared helper)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _dict_to_fix(fix: dict):
    """Rebuild a RecommendedFix from a stored dict (never raises)."""
    from .schemas import RecommendedFix as _RF
    try:
        return _RF(description=str(fix.get("description", "")),
                   diff=str(fix.get("diff")) if fix.get("diff") else None,
                   rationale=str(fix.get("rationale", "")),
                   diff_verified=bool(fix.get("diff_verified", False)))
    except Exception:
        return _RF(description="", diff=None)


def _parallel_reads(root: Path, targets: list,
                    max_bytes: int, cancel: threading.Event | None
                    ) -> list[tuple[str | None, str]]:
    """Many ``safe_read`` calls at once on a small bounded pool, in order.

    Reads are independent local-disk I/O — the single hottest loop in the
    pipeline — so a batch of frames/candidates goes out concurrently and
    comes back in submission order. Cancellation is checked before each
    result impression; a failing worker degrades to a ``(None, note)``
    instead of breaking the batch; a broken pool falls back to sequential
    reads. Behaviour is identical to a sequential loop, only faster.
    """
    if not targets:
        return []

    def job(target):
        return safe_read(root, target, max_bytes=max_bytes)

    if len(targets) == 1:
        _check_cancelled(cancel)
        return [job(targets[0])]
    try:
        with ThreadPoolExecutor(max_workers=max(2, min(8, len(targets))),
                                thread_name_prefix="trace-read") as ex:
            futs = [ex.submit(job, t) for t in targets]
            out: list[tuple[str | None, str]] = []
            for fut, _t in zip(futs, targets):
                _check_cancelled(cancel)
                try:
                    out.append(fut.result(timeout=60))
                except InvestigationCancelled:
                    raise
                except Exception:
                    out.append((None, "read failed"))
            return out
    except InvestigationCancelled:
        raise
    except Exception:
        out = []
        for t in targets:
            _check_cancelled(cancel)
            try:
                out.append(job(t))
            except Exception:
                out.append((None, "read failed"))
        return out


def _read_frame_files(root: Path, error: ErrorInfo,
                      seen: set[Path], files: list[tuple[Path, str]],
                      read_notes: list[str], max_bytes: int,
                      cancel: threading.Event | None) -> None:
    """Read stack-trace frames first (strongest signal), concurrently."""
    frames = error.frames[:8]
    contents = _parallel_reads(root, [frame.file for frame in frames],
                               max_bytes, cancel)
    for frame, (content, note) in zip(frames, contents):
        if content is not None:
            p = (root / frame.file).resolve() \
                if not Path(frame.file).is_absolute() else Path(frame.file)
            if p not in seen:
                files.append((p, content[:12000]))
                seen.add(p)
            read_notes.append(f"{frame.file}:{frame.line} ({note})")
        else:
            read_notes.append(f"{frame.file} — {note}")


# Bounded job sizes for the candidate-read stage (kept honest, not larger).
_CANDIDATE_READS = 20
_MAX_EVIDENCE_FILES = 12


def _read_candidate_files(root: Path, query: str,
                          seen: set[Path], files: list[tuple[Path, str]],
                          max_bytes: int,
                          cancel: threading.Event | None) -> None:
    """Ranked candidates read in one parallel batch; imports follow in a
    second parallel batch for the top hits. Ordering matches a sequential
    walk (candidates ranked first, then their imports), bounded as before.
    """
    candidates = list_candidate_files(root, query)[:_CANDIDATE_READS]
    ranked: list[tuple[Path, str | Path]] = []
    for cand in candidates:
        if cand in seen:
            continue
        try:
            rel = cand.relative_to(root)
        except ValueError:
            continue
        ranked.append((cand, rel))
    contents = _parallel_reads(root, [rel for _c, rel in ranked],
                               max_bytes, cancel)
    produced: list[Path] = []  # candidates that yielded content, in rank order
    for (cand, _rel), (content, _note) in zip(ranked, contents):
        if len(files) >= _MAX_EVIDENCE_FILES:
            break
        if content is None or cand in seen:
            continue
        files.append((cand, content[:12000]))
        seen.add(cand)
        produced.append(cand)

    # Follow imports of the top producing candidates (parallel second batch).
    import_jobs: list[tuple[Path, str | Path]] = []
    for cand in produced[:3]:
        for related in follow_imports(cand, root):
            if related in seen:
                continue
            if any(r == related for r, _t in import_jobs):
                continue
            if related.is_absolute():
                target: str | Path = related
            else:
                target = related.relative_to(root) \
                    if _is_within(related, root) else related
            import_jobs.append((related, target))
            if len(import_jobs) >= _MAX_EVIDENCE_FILES:
                break
        if len(import_jobs) >= _MAX_EVIDENCE_FILES:
            break
    if import_jobs:
        extras = _parallel_reads(root, [t for _r, t in import_jobs],
                                 max_bytes, cancel)
        for (related, _t), (extra, _n) in zip(import_jobs, extras):
            if len(files) >= _MAX_EVIDENCE_FILES:
                break
            if extra is None or related in seen:
                continue
            files.append((related, extra[:12000]))
            seen.add(related)


def _evidence_keywords(request_text: str, error: ErrorInfo) -> list[str]:
    """Identifier-ish tokens that are actually worth a project search."""
    seen: set[str] = set()
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}",
                       " ".join([request_text or "", error.message or ""]))
    stop = {"error", "exception", "traceback", "failed", "failure", "unknown"}
    out: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in stop or lw in seen:
            continue
        seen.add(lw)
        out.append(lw)
        if len(out) >= 6:
            break
    if error.message:  # error-message tokens outrank prose
        err_words = {w.lower() for w in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]{2,}", error.message)}
        ranked = sorted(out, key=lambda w: w not in err_words)
        out = ranked[:6]
    return out


def _gather_outlines(files: list[tuple[Path, str]], max_files: int = 8
                     ) -> list[tuple[Path, str, list, dict]]:
    """Outlines + complexity for several read files, concurrently."""

    def one(item):
        path, content = item
        try:
            outline = file_outline(path)[:12]
        except Exception:
            outline = []
        try:
            snap = complexity_snapshot(path, content)
        except Exception:
            snap = {}
        return path, content, outline, snap

    return [_r for _r in _parallel_map(one, files[:max_files]) if _r]


def _parallel_map(fn, items: list):
    """Order-preserving bounded map; a failing item yields None. Never raises."""
    if not items:
        return []
    if len(items) == 1:
        try:
            return [fn(items[0])]
        except Exception:
            return [None]
    try:
        with ThreadPoolExecutor(max_workers=max(2, min(8, len(items))),
                                thread_name_prefix="trace-engine") as ex:
            return list(ex.map(lambda it: _guarded_call(fn, it), items))
    except Exception:
        return [_try(fn, it) for it in items]


def _guarded_call(fn, item):
    try:
        return fn(item)
    except Exception:
        return None


def _try(fn, item):
    try:
        return fn(item)
    except Exception:
        return None


def _gather_intelligence(root: Path, request_text: str, error: ErrorInfo,
                         files: list[tuple[Path, str]]) -> str:
    """Bounded, disk-grounded supplementary evidence, collected concurrently.

    Sections — all capped and all real reads of this project:
      SEARCH HITS       keyword anchors across the whole tree (parallel).
      SYMBOLS           where error-token symbols are defined (parallel).
      outline/notes     one line per read file: shape + complexity notes.
      CONFIG CHECKS     top-level *.json syntax verdicts.
      ANNOTATIONS       TODO/FIXME anchors from the files already read.
      PROJECT TREE + RECENTLY MODIFIED — only when nothing could be read,
                        so the model can orient on honest disk truth
                        instead of inventing paths.
    """
    sections: list[str] = []

    # 1. Parallel whole-tree searches for the sharpest keywords.
    kws = _evidence_keywords(request_text, error)
    if kws:
        try:
            hits_by_kw = search_many(root, kws[:3], max_hits_each=6)
            lines = []
            for kw, hits in hits_by_kw.items():
                for h in hits:
                    lines.append(f"hit [{kw}] {h.path}:{h.line}: {h.snippet}")
            if lines:
                sections.append("SEARCH HITS:\n" + "\n".join(lines)[:2500])
        except Exception:
            pass

    # 2. Symbol definitions for error tokens (concurrent).
    try:
        err_kws = [w for w in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]{2,}", error.message or "")][:3]
        if err_kws:
            defs = find_symbols(root, err_kws, limit=3)
            lines = []
            for sym, hits in defs.items():
                for hit in hits:
                    lines.append(f"symbol {sym}: {hit.path}:{hit.line}: "
                                 f"{hit.snippet}")
            if lines and sum(len(x) for x in lines) < 2200:
                sections.append("SYMBOLS:\n" + "\n".join(lines))
    except Exception:
        pass

    # 3. Outlines + complexity notes for the files we actually read.
    try:
        for path, content, outline, snap in _gather_outlines(files):
            if not outline and not snap.get("notes"):
                continue
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            if outline:
                desc = ", ".join(f"{e.kind} {e.name}@{e.line}"
                                 for e in outline)
                sections.append(f"outline {rel}: {desc}")
            notes = snap.get("notes") if isinstance(snap, dict) else None
            if notes:
                sections.append(f"notes {rel}: {'; '.join(notes)}")
    except Exception:
        pass

    if len("\n".join(sections)) > 3400:
        return "CODE INTELLIGENCE:\n" + "\n".join(sections)[:3500]

    # 4. Top-level JSON config validation (cheap; only when room remains).
    try:
        cfg_notes = check_json_config(root)
        if cfg_notes:
            sections.append("CONFIG CHECKS:\n" + "\n".join(cfg_notes)[:1000])
    except Exception:
        pass

    # 5. TODO/FIXME anchors in the already-read contents.
    try:
        try:
            annot_items = [(str(p.relative_to(root)), c)
                           for p, c in files]
        except ValueError:
            annot_items = [(str(p), c) for p, c in files]
        notes_ = scan_annotations(annot_items, max_notes=15)
        if notes_:
            sections.append("ANNOTATIONS:\n" + "\n".join(
                f"{n['path']}:{n['line']}: {n['note']}" for n in notes_)[:1200])
    except Exception:
        pass

    # 6. Orientation aids when no file could be read at all — honest disk
    #    truth (bounds + excludes applied), never invented paths.
    if not files:
        try:
            tree = project_tree(root)
            if tree:
                sections.append("PROJECT TREE (readable files only):\n" + tree)
        except Exception:
            pass
        try:
            rec = recent_files(root, limit=8)
            if rec:
                lines = [f"{r['path']} (modified {r['minutes_ago']} min ago)"
                         for r in rec]
                sections.append("RECENTLY MODIFIED:\n" + "\n".join(lines)[:1200])
        except Exception:
            pass

    if not sections:
        return ""
    return "CODE INTELLIGENCE:\n" + "\n".join(sections)[:4000]


def _maybe_auto_fix(root: Path | None, report: InvestigationReport,
                    auto_fix: bool, allow_edits: bool) -> InvestigationReport:
    """Apply the verified fix diff when auto_fix was requested. Never raises."""
    if not auto_fix:
        return report
    if not allow_edits:
        report.warnings.append(
            "Auto-fix requested but file edits are disabled "
            "(TRACE_ALLOW_FILE_EDITS=0); showing the proposed patch only.")
        report.fix_applied = {"applied": False, "applied_files": [],
                              "backup_paths": [], "dry_run": False,
                              "warnings": ["file edits disabled"]}
        return report
    fix = report.recommended_fix
    if fix is None or not (fix.diff or "").strip():
        report.warnings.append("Auto-fix requested but no fix diff was proposed.")
        report.fix_applied = {"applied": False, "applied_files": [],
                              "backup_paths": [], "dry_run": False,
                              "warnings": ["no diff proposed"]}
        return report
    if not fix.diff_verified:
        report.warnings.append(
            "Auto-fix skipped: the proposed patch could not be verified "
            "against the files on disk.")
        report.fix_applied = {"applied": False, "applied_files": [],
                              "backup_paths": [], "dry_run": False,
                              "warnings": ["diff unverified"]}
        return report
    try:
        result = apply_unified_diff(root, fix.diff)
    except Exception as e:  # pragma: no cover - code_fixer never raises
        report.warnings.append(f"Auto-fix failed: {e}")
        report.fix_applied = {"applied": False, "applied_files": [],
                              "backup_paths": [], "dry_run": False,
                              "warnings": [str(e)]}
        return report
    report.fix_applied = result.to_dict()
    if result.applied:
        report.warnings.append(
            "Auto-fix applied to: " + ", ".join(result.applied_files)
            + ". Backups kept alongside each file (*.trace-bak).")
    else:
        report.warnings.extend(result.warnings or
                               ["Auto-fix could not be applied."])
    return report


# -- agent graph: state, nodes, wiring ---------------------------------------
# The pipeline is expressed as an explicit, auditable graph (agent_graph):
# named nodes over shared typed state, conditional routing, and parallel
# fan-out/fan-in where collection is genuinely independent. Node functions
# reference module-level tool names (collect_diagnostics, run_project_tests,
# capture_screenshot, extract_error_info, collect_local_context) so the tests
# can keep patching them at the module boundary.

@dataclass
class AgentState:
    """Everything one investigation carries through the graph.

    Evidence fields are filled by their owning node; ``abort`` + ``report``
    terminate the run with an honest verdict (never a fabricated one);
    ``failures`` remember degraded *collection* steps so the final report
    can say exactly what was attempted and what broke.
    """
    request: InvestigationRequest
    config: TraceConfig
    ollama: Any
    inv_id: str
    cancel: threading.Event | None
    emit: Callable[[str, str], None]
    mode: str = "debug"  # "debug" | "explain"
    # capture fan-out results
    shot: ScreenshotResult | None = None
    # The screenshot branch records its result here the instant it exists, so
    # a cancellation that lands before the merge can still be disposed by the
    # engine's finally (otherwise the temp file leaked on disk).
    pending_shot: ScreenshotResult | None = None
    screenshot_b64: str | None = None
    screenshot_note: str = "screenshots disabled by request"
    ctx: LocalContext = field(default_factory=LocalContext)
    # root / extract / read
    root: Path | None = None
    error: ErrorInfo = field(default_factory=ErrorInfo)
    files: list = field(default_factory=list)
    read_notes: list = field(default_factory=list)
    seen: set = field(default_factory=set)
    # collect fan-out results
    diagnostics: list = field(default_factory=list)
    intelligence: str = ""
    test_note: str = ""
    # analysis
    raw: str = ""
    repair_tried: int = 0
    # termination / honesty notes
    report: InvestigationReport | None = None
    abort: bool = False
    failures: list = field(default_factory=list)
    needs_more: list = field(default_factory=list)
    # graph bookkeeping (runtime attaches the live GraphRun here)
    run_trace: Any = None


REPAIR_SYSTEM_PROMPT = """You are Trace. Your previous reply was not valid JSON. \
Return ONLY valid JSON with this exact schema and nothing else (no prose, no code fences):
{"summary": str, "root_cause": str, "confidence": "low"|"medium"|"high",
 "confidence_rationale": str, "findings": [{"title": str, "file_path": str,
 "start_line": int, "end_line": int, "explanation": str, "evidence": str}],
 "recommended_fix": {"description": str, "diff": str, "rationale": str},
 "alternative_hypotheses": [str], "needs_more_info": [str], "evidence_summary": str}
Grounding rules are unchanged: only cite file paths and line numbers that appeared in the \
evidence bundle you were given. If you cannot reconstruct a full report, return the JSON with \
confidence "low" and empty findings — never free text."""


def _node_receive(state: AgentState) -> str:
    """Validate the request; abort honestly when it is unusable."""
    _check_cancelled(state.cancel)
    is_explain = state.mode == "explain"
    state.emit("receive", "Received code explanation request." if is_explain
               else "Received debugging request.")
    problems = state.request.validate()
    if problems:
        state.report = InvestigationReport(
            investigation_id=state.inv_id,
            summary="Invalid investigation request.",
            root_cause="; ".join(problems), confidence="low",
            warnings=["Invalid request: " + "; ".join(problems)])
        state.abort = True
        return END
    return "capture_start"


def _node_capture_start(state: AgentState) -> str:
    """Announce the two capture stages, then fan out — canonical order."""
    _check_cancelled(state.cancel)
    if state.request.include_screenshot:
        state.emit("screenshot",
                   f"Capturing {state.request.screenshot_mode} screenshot…")
    else:
        state.emit("screenshot", "screenshots disabled by request")
    state.emit("context", "Collecting local context…")
    return "capture"


def _capture_screenshot_branch(state: AgentState) -> tuple:
    """Independent branch: capture the screenshot. Never raises."""
    if not state.request.include_screenshot:
        return ("idle", None)
    try:
        shot = capture_screenshot(mode=state.request.screenshot_mode,
                                  max_dim=state.config.max_screenshot_dim)
        # Record BEFORE the merge: if cancel fires between this return and
        # the merge, the engine's finally can still dispose the temp file.
        state.pending_shot = shot
        return ("ok", shot)
    except (ScreenshotCaptureError, ValueError) as e:
        hint = getattr(e, "hint", "")
        return ("unavailable", f"screenshot unavailable: {e}. {hint}".strip())
    except Exception as e:
        return ("failed", f"screenshot failed unexpectedly: {e}")


def _capture_context_branch(state: AgentState) -> tuple:
    """Independent branch: local context (window/editor/terminal inputs)."""
    try:
        ctx = collect_local_context(
            editor_selection=state.request.editor_selection,
            terminal_output=state.request.terminal_output,
            allow_clipboard=state.request.include_clipboard,
            project_hint=state.request.project_dir)
        return ("ok", ctx)
    except Exception as e:
        return ("failed", LocalContext())


def _capture_merge(state: AgentState, results: dict,
                   timings: dict | None = None) -> str:
    """Join the screenshot ∥ context fan-out; emit notes in canonical order."""
    kind, payload = results.get("screenshot") or ("failed", None)
    if kind == "ok":
        state.shot = payload
        state.screenshot_b64 = base64.b64encode(payload.png_bytes).decode("ascii")
        state.screenshot_note = (f"screenshot captured via {payload.source} "
                                 f"({payload.width}x{payload.height}, "
                                 f"{len(payload.png_bytes)} bytes)")
    elif kind == "unavailable":
        state.screenshot_note = payload
        state.emit("screenshot", payload)
    elif kind == "failed":
        state.screenshot_note = payload
    # emit("screenshot", start-note) happened before the fan-out already.

    ctx_kind, ctx_payload = results.get("context") or ("failed", None)
    if ctx_kind == "ok":
        state.ctx = ctx_payload
    else:
        state.failures.append(
            "Local context collection failed; continuing without it.")

    _check_cancelled(state.cancel)
    return "project_root"


def _node_project_root(state: AgentState) -> str:
    _check_cancelled(state.cancel)
    state.emit("project_root", "Identifying project root…")
    root = find_project_root(state.request.project_dir or state.ctx.cwd)
    if root is None:
        msg = ("Could not identify the active project. Select the project "
               "folder or provide the missing context.")
        state.report = InvestigationReport(
            investigation_id=state.inv_id,
            summary="Project root could not be identified.",
            root_cause="Unknown project location; no files inspected.",
            confidence="low",
            confidence_rationale=("No project root was identified, so no code "
                                  "could be inspected for evidence."),
            needs_more_info=[msg],
            evidence_summary=f"{state.screenshot_note}\n{state.ctx.summary()}",
            warnings=["No project files were read; refusing to invent an explanation."])
        state.abort = True
        return END
    state.root = root
    return "extract"


def _node_extract(state: AgentState) -> str:
    """Stack-trace extraction — skipped (honestly) in explain mode."""
    _check_cancelled(state.cancel)
    if state.mode == "explain":
        state.emit("error_extract", "Skipping error extraction (explain mode).")
        return "read"
    state.emit("error_extract", "Extracting error message and stack trace…")
    combined_text = "\n\n".join(t for t in [
        state.request.request_text, state.request.terminal_output or "",
        state.request.editor_selection or "", state.request.extra_context or ""] if t)
    state.error = extract_error_info(combined_text)
    return "read"


def _node_read(state: AgentState) -> str:
    _check_cancelled(state.cancel)
    state.emit("read_files", f"Reading relevant files under {state.root}…")
    is_explain = state.mode == "explain"
    if not is_explain:
        _read_frame_files(state.root, state.error, state.seen, state.files,
                          state.read_notes, state.config.max_file_bytes,
                          state.cancel)
    query = state.request.request_text + (
        " " + state.error.message
        if not is_explain and state.error.message else "")
    _read_candidate_files(state.root, query, state.seen, state.files,
                          state.config.max_file_bytes, state.cancel)
    if not state.files:
        state.needs_more.append(
            "No relevant project files could be identified. Select the project "
            "folder, paste the error output, or name the file you suspect.")
    return "collect"


def _branch_diagnostics(state: AgentState) -> tuple:
    """Linter/compiler diagnostics — independent subprocess batch."""
    try:
        results = collect_diagnostics(
            state.root, [p for p, _ in state.files],
            timeout_s=state.config.diagnostics_timeout_s)
        return ("ok", results)
    except Exception as e:
        return ("fail", e)


def _branch_tests(state: AgentState) -> tuple:
    """Allowlisted test run — independent subprocess; debug mode only.

    Explain mode must never reach the runner (the engine keeps
    understanding separate from verification).
    """
    if state.mode != "explain" and state.request.run_tests:
        try:
            res = run_project_tests(
                state.root, timeout_s=state.config.diagnostics_timeout_s,
                allowed=state.config.allow_test_execution)
            return ("ok", res)
        except Exception as e:
            return ("fail", e)
    return ("skipped", None)


def _branch_intelligence(state: AgentState) -> str:
    try:
        return _gather_intelligence(state.root, state.request.request_text,
                                    state.error, state.files)
    except Exception:
        return ""


def _collect_merge(state: AgentState, results: dict,
                   timings: dict | None = None) -> str:
    """Join diagnostics ∥ tests ∥ intelligence; keep the original evidence
    order and the original opt-in honest notes."""
    _check_cancelled(state.cancel)
    state.emit("diagnostics", "Collecting compiler/linter diagnostics…")
    diag_kind, diag_payload = results.get("diagnostics") or ("fail", None)
    if diag_kind == "ok":
        state.diagnostics = diag_payload or []
    else:
        state.failures.append(
            f"Diagnostics collection failed: {diag_payload}")

    test_kind, test_res = results.get("tests") or ("skipped", None)
    if test_kind == "ok" and test_res:
        if test_res.get("ran"):
            state.test_note = (f"TESTS ({' '.join(test_res['command'])}): "
                               f"{test_res.get('reason', '')}\n"
                               f"{test_res.get('output', '')[:3000]}")
            # Grounded failure anchors straight from the runner's output.
            try:
                anchors = parse_test_failures(test_res.get("output", ""))
                if anchors:
                    state.test_note += "\nTEST FAILURE ANCHORS:\n" + "\n".join(
                        f"{a.path}:{a.line}" for a in anchors)
            except Exception:
                pass
        else:
            state.test_note = f"TESTS skipped: {test_res.get('reason', '')}"
            state.needs_more.append(
                "Test execution is opt-in (TRACE_ALLOW_TESTS=1); "
                "enable it to include test output as evidence.")
    elif test_kind == "fail":
        state.failures.append(f"Test run failed: {test_res}")

    state.intelligence = results.get("intelligence") or ""
    return "analyze"


# Live reasoning ticks: throttled so the SSE stream and UI logs see
# meaningful chunks of the model's thinking instead of every token. Three
# bounds: emit at most every this-many accumulated characters ...
_STREAM_TICK_CHARS = 480
# ... at most this-many ticks per model call ...
_STREAM_TICK_MAX = 24
# ... and each tick shows only this many newest characters (the tail).
_STREAM_TICK_TAIL = 90


def _stream_thinker(emit: Callable[[str, str], None]) -> Callable[[str], None]:
    """Return an ``on_token`` callback that emits throttled reasoning ticks.

    Every ``_STREAM_TICK_CHARS`` accumulated characters (up to
    ``_STREAM_TICK_MAX`` ticks) one ``model_analysis`` event carries the
    newest ``_STREAM_TICK_TAIL`` characters of the model's output — the
    user sees the reasoning breathe instead of a silent gap. Emission
    failures must never break the stream, so the callback itself never
    raises.
    """
    buf: list[str] = []
    chars = 0
    left = _STREAM_TICK_MAX

    def on_token(delta: str) -> None:
        nonlocal chars, left
        buf.append(delta)
        chars += len(delta)
        if left > 0 and chars >= _STREAM_TICK_CHARS:
            left -= 1
            chars = 0
            tail = " ".join("".join(buf)[-_STREAM_TICK_TAIL:].split())
            if tail:
                try:
                    emit("model_analysis", f"thinking ▸ …{tail}")
                except Exception:
                    pass
    return on_token


def _node_analyze(state: AgentState) -> str:
    """Assemble the evidence bundle and ask the model for a structured verdict."""
    state.emit("model_analysis", "Thinking through the evidence…")
    is_explain = state.mode == "explain"
    bundle = build_model_context(
        request_text=state.request.request_text, error=state.error,
        files=state.files, diagnostics=state.diagnostics,
        local_summary=state.ctx.summary() + "\n" + state.screenshot_note,
        budget_chars=state.config.max_context_chars)
    # intelligence/test_note are raw disk text (search hits, TODO lines, test
    # output); redact them like the rest of the bundle before the model call.
    for extra in (state.intelligence, state.test_note):
        if extra:
            bundle = (bundle + "\n\n---\n\n"
                      + redact_secrets(extra))[:state.config.max_context_chars]
    if is_explain:
        user_prompt = (bundle + "\n\n---\n\n"
                       "Explain the code above and return ONLY the JSON report. "
                       "Describe what the code does, how it works, and any "
                       "notable behavior or potential issues. Do not invent "
                       "file paths or line numbers.")
        system_prompt = EXPLAIN_SYSTEM_PROMPT
    else:
        user_prompt = (bundle + "\n\n---\n\n"
                       "Analyze the evidence above and return ONLY the JSON report. "
                       "Do not invent file paths or line numbers. "
                       "Propose a minimal unified diff that applies cleanly.")
        system_prompt = SYSTEM_PROMPT
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]
    if state.screenshot_b64 and state.shot is not None:
        # Ollama vision: attach image to the user message when supported.
        messages[-1] = {"role": "user", "content": user_prompt,
                        "images": [state.screenshot_b64]}
    try:
        # Streaming with throttled reasoning ticks — the SSE stage events
        # make the previously silent "Thinking…" phase observable. The
        # returned value is still the full assistant text, so parsing and
        # verification below are unchanged.
        state.raw = state.ollama.chat(messages, stream=True,
                                      on_token=_stream_thinker(state.emit),
                                      cancel_event=state.cancel,
                                      timeout=state.config.read_timeout_s)
    except OllamaCancelledError as e:
        raise InvestigationCancelled(str(e)) from e
    except OllamaError as e:
        hint = getattr(e, "hint", "")
        state.report = InvestigationReport(
            investigation_id=state.inv_id,
            summary="Model analysis unavailable.",
            root_cause=str(e), confidence="low",
            confidence_rationale="No model verdict; evidence collected but not analyzed.",
            needs_more_info=[hint] if hint else [],
            evidence_summary=(f"{state.screenshot_note}\n{state.ctx.summary()}\n"
                              f"Files read: {len(state.files)}. "
                              + "; ".join(state.read_notes)[:2000]),
            warnings=[f"Ollama error: {e}"])
        state.abort = True
        return END
    return "validate"


def _findings_are_malformed(report: InvestigationReport) -> bool:
    """True when the model reply could not be shaped into a verdict."""
    return any(w.startswith("Malformed model output") for w in report.warnings)


def _node_validate(state: AgentState) -> str:
    """The grounded-truth gate: parse, verify, route to fix/report/repair."""
    _check_cancelled(state.cancel)
    state.emit("validate", "Validating file paths and line numbers…")
    report = InvestigationEngine._parse_report(state.inv_id, state.raw)
    if _findings_are_malformed(report) and state.repair_tried < 1:
        state.repair_tried += 1
        return "repair"  # bounded self-repair loop: one clean re-ask
    report = sanitize_report(state.root, report)
    _enrich_git_context(state.root, report)
    state.report = report
    if state.request.auto_fix:
        return "apply_fix"
    return "report"


def _node_repair(state: AgentState) -> str:
    """Second chance for malformed JSON — the verdict must stay verifiable."""
    state.emit("validate", "Model output was not valid JSON; asking for repair…")
    try:
        state.raw = state.ollama.chat(
            [{"role": "system", "content": REPAIR_SYSTEM_PROMPT},
             {"role": "user", "content": (
                 "Rebuild your full report as ONLY valid JSON. "
                 "Your previous (invalid) reply, for reference:\n\n"
                 f"{state.raw[:4000]}")}],
            stream=True, on_token=_stream_thinker(state.emit),
            cancel_event=state.cancel,
            timeout=state.config.read_timeout_s)
    except OllamaCancelledError as e:
        raise InvestigationCancelled(str(e)) from e
    except OllamaError:
        pass  # repair is best-effort; validation degrades honestly below
    return "validate"


def _node_apply_fix(state: AgentState) -> str:
    state.emit("validate", "Applying the verified fix…")
    state.report = _maybe_auto_fix(state.root, state.report, True,
                                   state.config.allow_file_edits)
    return "report"


def _node_report(state: AgentState) -> str:
    """Finalize: honest evidence summary, collected notes, collected failures."""
    state.emit("report", "Investigation complete.")
    report = state.report
    if report is None:
        report = InvestigationReport(
            investigation_id=state.inv_id,
            summary="Investigation ended without a verdict.",
            root_cause="Unknown.", confidence="low",
            warnings=["The graph finished without producing a report."])
        state.report = report
    if not report.evidence_summary:
        report.evidence_summary = (
            f"{state.screenshot_note}\n{state.ctx.summary()}\n"
            f"Project root: {state.root}\nFiles read: {len(state.files)}")
    if state.needs_more:
        report.needs_more_info = [*report.needs_more_info, *state.needs_more]
    if len(state.files) == 0:
        report.warnings.append(
            "Screenshot/visual evidence alone is insufficient; no code excerpts "
            "were available for verification.")
    for failure in state.failures:
        report.warnings.append(failure)
    if state.run_trace is not None and hasattr(state.run_trace, "summary"):
        report.evidence_summary = (
            report.evidence_summary + "\n" + state.run_trace.summary())[:4000]
        timings_text = state.run_trace.timings_text()
        if timings_text:
            report.evidence_summary = (
                report.evidence_summary + f"\nTiming: {timings_text}")[:4000]
    return END


def _build_investigation_graph() -> "Graph":
    g = Graph("trace-investigation", cancel_exc_type=InvestigationCancelled)
    g.add_node("receive", _node_receive)
    g.add_node("capture_start", _node_capture_start)
    g.add_parallel(
        "capture",
        {"screenshot": _capture_screenshot_branch,
         "context": _capture_context_branch},
        merge=_capture_merge)
    g.add_node("project_root", _node_project_root)
    g.add_node("extract", _node_extract)
    g.add_node("read", _node_read)
    g.add_parallel(
        "collect",
        {"diagnostics": _branch_diagnostics,
         "tests": _branch_tests,
         "intelligence": _branch_intelligence},
        merge=_collect_merge)
    g.add_node("analyze", _node_analyze)
    g.add_node("validate", _node_validate)
    g.add_node("repair", _node_repair)
    g.add_node("apply_fix", _node_apply_fix)
    g.add_node("report", _node_report)
    return g


_GRAPH = _build_investigation_graph()


class InvestigationEngine:
    def __init__(self, config: TraceConfig | None = None,
                 ollama: OllamaClient | None = None):
        self.config = config or TraceConfig.from_env()
        self.ollama = ollama or OllamaClient(self.config)

    # Graph node names, in execution order. The UI's five-step rail is keyed
    # by stage names inside the events; these states map 1:1 onto them.
    _GRAPH_NODES = ("receive", "capture(screenshot∥context)", "project_root",
                    "extract", "read", "collect(diagnostics∥tests∥intelligence)",
                    "analyze", "validate→repair?", "apply_fix?", "report")

    def investigate(self, request: InvestigationRequest,
                    progress_cb: ProgressCallback | None = None,
                    cancel_event: threading.Event | None = None,
                    investigation_id: str | None = None) -> InvestigationReport:
        """Run the investigation through the agent graph (see agent_graph.py).

        Same signature, same stage events, same honest-failure semantics as
        before — the graph makes the routing, parallelism and the bounded
        repair loop explicit instead of burying them in nested ifs.
        """
        inv_id = investigation_id or new_investigation_id()

        def emit(stage: str, message: str):
            idx = STAGES.index(stage) if stage in STAGES else 0
            progress = (idx + 1) / len(STAGES)
            if progress_cb:
                try:
                    progress_cb(ProgressEvent(inv_id, stage, message, progress))
                except Exception:
                    pass

        state = AgentState(
            request=request, config=self.config, ollama=self.ollama,
            inv_id=inv_id, cancel=cancel_event, emit=emit,
            mode=request.investigation_type)

        try:
            try:
                _GRAPH.run(state, start="receive", cancel=cancel_event,
                           max_steps=self.config.max_iterations)
            finally:
                if state.shot is not None:
                    dispose(state.shot)
                elif state.pending_shot is not None:
                    # The merge never promoted it to state.shot — dispose
                    # the pending capture so its temp file cannot leak.
                    dispose(state.pending_shot)
            if state.report is not None:
                return state.report
            # Defensive: the graph exhausted routing without a verdict.
            return InvestigationReport(
                investigation_id=inv_id,
                summary="Investigation ended without a verdict.",
                root_cause="Unknown.", confidence="low",
                warnings=["The agent graph ended without a report."])
        except InvestigationCancelled:
            raise

    def follow_up(self, request: InvestigationRequest,
                  prior_report: InvestigationReport | dict | None,
                  emit: Callable[[str, str], None] | None = None,
                  cancel_event: threading.Event | None = None,
                  investigation_id: str | None = None) -> InvestigationReport:
        """Answer a follow-up question about an existing verdict.

        Unlike :meth:`investigate` this does not re-collect the project from
        scratch: the prior report *is* the context. Grounded-truth discipline is
        unchanged — the model may only cite what the prior report verified or
        what is re-read from disk here, and every finding in its answer is run
        through ``validator.sanitize_report`` like any other report.
        """
        inv_id = investigation_id or new_investigation_id()
        parent = request.follow_up_of or ""

        def say(stage: str, message: str):
            if emit:
                try:
                    emit(stage, message)
                except Exception:
                    pass

        problems = request.validate()
        if problems:
            return InvestigationReport(
                investigation_id=inv_id, summary="Invalid follow-up request.",
                root_cause="; ".join(problems), confidence="low",
                warnings=["Invalid request: " + "; ".join(problems)],
                follow_up_of=parent)

        # 1. receive
        _check_cancelled(cancel_event)
        say("receive", f"Received follow-up question about {parent or 'prior report'}.")
        prior = _prior_as_dict(prior_report)

        # 2. project root — same tree, so locations stay verifiable
        _check_cancelled(cancel_event)
        root: Path | None = None
        if request.project_dir:
            root = find_project_root(request.project_dir)
        needs_more: list[str] = []
        if root is None:
            note = ("Project root could not be identified, so follow-up findings "
                    "cannot be verified against the project files.")
            say("project_root", note)
            needs_more.append(note)
        else:
            say("project_root", f"Verifying against project root {root}…")

        # 3. re-read the files the prior report cited (bounded, disk-verified)
        files: list[tuple[Path, str]] = []
        read_notes: list[str] = []
        seen: set[Path] = set()
        if root is not None:
            say("read_files", "Re-reading the files cited by the prior report…")
            cited: list[dict] = []
            for f in (prior.get("findings") or [])[:6]:
                _check_cancelled(cancel_event)
                if not isinstance(f, dict):
                    continue
                rel = str(f.get("file_path", "")).strip()
                if not rel:
                    continue
                cited.append(f)
            # The re-reads are independent local reads: one parallel batch,
            # results in findings order, same honest notes when unreadable.
            contents = _parallel_reads(
                root, [str(f.get("file_path", "")).strip() for f in cited],
                self.config.max_file_bytes, cancel_event)
            for f, (content, note) in zip(cited, contents):
                rel = str(f.get("file_path", "")).strip()
                if content is None:
                    read_notes.append(f"{rel} — {note}")
                    continue
                resolved = resolve_within_root(root, rel)
                p = resolved if resolved is not None else root / rel
                if p not in seen:
                    try:
                        start = int(f.get("start_line", 0) or 0)
                        end = int(f.get("end_line", 0) or start)
                    except (TypeError, ValueError):
                        start, end = 1, 60
                    excerpt = _numbered(content, max(1, start - 25), end + 25) \
                        or _numbered(content, 1, 60)
                    files.append((p, excerpt))
                    seen.add(p)
                read_notes.append(f"{rel} ({note})")
        extra = "\n".join(t for t in [request.terminal_output or "",
                                      request.editor_selection or "",
                                      request.extra_context or ""] if t)

        # 4. model analysis
        say("model_analysis", "Reconsidering the prior verdict…")
        parts = [f"FOLLOW-UP QUESTION:\n{request.request_text[:4000]}",
                 _prior_report_text(prior)]
        for p, excerpt in files:
            parts.append(f"FILE (re-read from disk, numbered lines): {p}\n{excerpt}")
        if extra:
            parts.append(f"ADDITIONAL CONTEXT:\n{extra[:4000]}")
        if read_notes:
            parts.append("RE-READ NOTES:\n" + "; ".join(read_notes)[:2000])
        if needs_more:
            parts.append("LIMITATION:\n" + "; ".join(needs_more))
        bundle = redact_secrets(
            "\n\n---\n\n".join(parts)[:self.config.max_context_chars])
        user_prompt = (bundle + "\n\n---\n\nAnswer the FOLLOW-UP QUESTION and return ONLY "
                        "the JSON report. Do not invent file paths or line numbers.")
        try:
            raw = self.ollama.chat(
                [{"role": "system", "content": FOLLOW_UP_SYSTEM_PROMPT},
                 {"role": "user", "content": user_prompt}],
                stream=True, on_token=_stream_thinker(say),
                cancel_event=cancel_event,
                timeout=self.config.read_timeout_s)
        except OllamaCancelledError as e:
            raise InvestigationCancelled(str(e)) from e
        except OllamaError as e:
            hint = getattr(e, "hint", "")
            return InvestigationReport(
                investigation_id=inv_id,
                summary="Follow-up could not be answered: the model is unavailable.",
                root_cause=str(e), confidence="low",
                confidence_rationale="No model verdict; the prior report stands unchanged.",
                needs_more_info=[hint] if hint else [],
                evidence_summary=_prior_report_text(prior) + f"\nModel error: {e}",
                warnings=[f"Ollama error: {e}"], follow_up_of=parent)

        # 5. validate — the same grounded-truth gate as a first-pass report
        _check_cancelled(cancel_event)
        say("validate", "Validating file paths and line numbers…")
        report = self._parse_report(inv_id, raw)
        report = sanitize_report(root, report)
        report.follow_up_of = parent
        # Add grounded git "last changed" context to verified findings.
        _enrich_git_context(root, report)
        if not report.evidence_summary:
            report.evidence_summary = (
                f"Follow-up on {parent or 'prior report'}. "
                + (f"Project root: {root}. " if root is not None
                   else "Project root unknown; locations could not be verified. ")
                + f"Files re-read from disk: {len(files)}. "
                + _prior_report_text(prior)[:1000])
        if needs_more:
            report.needs_more_info = [*report.needs_more_info, *needs_more]
        if not prior:
            report.warnings.append(
                "No prior report was supplied; this answer is based on the "
                "follow-up question and disk excerpts alone.")

        # 6. report
        say("report", "Follow-up complete.")
        return report

    # -- apply-fix ----------------------------------------------------------
    def apply_fix(self, report: InvestigationReport | dict,
                  project_dir: str | None,
                  *, dry_run: bool = False) -> dict:
        """Apply a completed report's verified fix diff to disk.

        Keeps the HTTP layer thin: root resolution + edit guard + diff
        application all live here (the engine already owns root + validator).
        Never raises: failures become ``{"ok": False, ...}``.
        """
        try:
            rep = report if isinstance(report, dict) else report.to_dict()
        except Exception:
            return {"ok": False, "applied": False, "reason": "bad report"}
        if not self.config.allow_file_edits:
            return {"ok": False, "applied": False,
                    "reason": "file edits are disabled (TRACE_ALLOW_FILE_EDITS=0)"}
        fix = (rep.get("recommended_fix") or {}) if isinstance(rep, dict) else {}
        diff = fix.get("diff") if isinstance(fix, dict) else None
        if not diff or not str(diff).strip():
            return {"ok": False, "applied": False, "reason": "no fix diff proposed"}
        root = find_project_root(project_dir) if project_dir else None
        if root is None:
            return {"ok": False, "applied": False,
                    "reason": "project root could not be identified"}
        if not bool(fix.get("diff_verified", False)):
            # Re-ground right now: the file may have changed since diagnosis.
            from .validator import verify_fix_diff
            ok, warnings = verify_fix_diff(root, _dict_to_fix(fix))
            if not ok:
                return {"ok": False, "applied": False,
                        "reason": "diff could not be verified against current files",
                        "warnings": warnings}
        result = apply_unified_diff(root, str(diff), dry_run=bool(dry_run))
        out = result.to_dict()
        out["ok"] = bool(result.applied) if not dry_run else True
        if not dry_run and not result.applied:
            out["ok"] = False
            out.setdefault("reason", "; ".join(result.warnings) or "not applied")
        return out

    # -- parsing ------------------------------------------------------------
    @staticmethod
    def _parse_report(inv_id: str, raw: str) -> InvestigationReport:
        text = raw.strip()
        # Strip code fences.
        if text.startswith("```"):
            first_nl = text.find("\n")
            text = text[first_nl + 1:] if first_nl != -1 else text[3:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[: -3]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to salvage the largest {...} block.
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    data = None
            else:
                data = None
        if not isinstance(data, dict):
            return InvestigationReport(
                investigation_id=inv_id,
                summary="Model returned unstructured output.",
                root_cause="Could not parse a structured verdict from the model.",
                confidence="low",
                confidence_rationale="Malformed model output; no verified findings.",
                alternative_hypotheses=[text[:2000] if text else "(empty model output)"],
                needs_more_info=["Re-run the investigation; consider a smaller evidence bundle."],
                warnings=["Malformed model output: expected JSON, got free text. "
                          "Raw output preserved as an alternative hypothesis."])
        try:
            # Shape checks: parses-but-wrong JSON (e.g. `{"findings": "none"}`)
            # previously became a silent empty verdict with no repair chance.
            shape_warnings: list[str] = []
            findings_raw = data.get("findings", [])
            if not isinstance(findings_raw, list):
                shape_warnings.append("findings was not a list")
                findings_raw = []
            alt_raw = data.get("alternative_hypotheses", [])
            if not isinstance(alt_raw, list):
                shape_warnings.append("alternative_hypotheses was not a list")
                alt_raw = []
            more_raw = data.get("needs_more_info", [])
            if not isinstance(more_raw, list):
                shape_warnings.append("needs_more_info was not a list")
                more_raw = []
            findings = []
            for f in findings_raw:
                if not isinstance(f, dict):
                    continue
                try:
                    findings.append(Finding(
                        title=str(f.get("title", "Finding"))[:300],
                        file_path=str(f.get("file_path", ""))[:500],
                        start_line=int(f.get("start_line", 0) or 0),
                        end_line=int(f.get("end_line", 0) or int(f.get("start_line", 0) or 0)),
                        explanation=str(f.get("explanation", ""))[:4000],
                        evidence=str(f.get("evidence", ""))[:4000],
                        confidence=str(f.get("confidence", "low")).lower()
                        if str(f.get("confidence", "")).lower() in ("low", "medium", "high") else "low",
                    ))
                except (ValueError, TypeError):
                    continue
            rec = data.get("recommended_fix") or {}
            recommended = None
            if not isinstance(rec, dict):
                shape_warnings.append("recommended_fix was not an object")
                rec = {}
            elif rec.get("description") or rec.get("diff"):
                recommended = RecommendedFix(
                    description=str(rec.get("description", ""))[:4000],
                    diff=str(rec.get("diff"))[:8000] if rec.get("diff") else None,
                    rationale=str(rec.get("rationale", ""))[:2000])
            conf = str(data.get("confidence", "low")).lower()
            if conf not in ("low", "medium", "high"):
                conf = "low"
            return InvestigationReport(
                investigation_id=inv_id,
                summary=str(data.get("summary", ""))[:4000] or "No summary provided.",
                root_cause=str(data.get("root_cause", ""))[:4000] or "Unknown.",
                confidence=conf,
                confidence_rationale=str(data.get("confidence_rationale", ""))[:2000],
                findings=findings,
                recommended_fix=recommended,
                alternative_hypotheses=[str(h)[:2000] for h in alt_raw][:10],
                needs_more_info=[str(n)[:1000] for n in more_raw][:10],
                evidence_summary=str(data.get("evidence_summary", ""))[:4000],
                warnings=(["Malformed model output: " + "; ".join(shape_warnings)
                           + "."] if shape_warnings else []),
            )
        except Exception as e:
            return InvestigationReport(
                investigation_id=inv_id,
                summary="Model output could not be interpreted.",
                root_cause=f"Report shaping failed: {e}",
                confidence="low",
                alternative_hypotheses=[text[:2000]],
                warnings=[f"Report shaping failed: {e}"])


def _in(p: Path, root: Path) -> bool:
    """Legacy alias for :func:`_is_within` (kept for external imports)."""
    return _is_within(p, root)
