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
- ``follow_up`` answers a question about an existing verdict: the prior
  report is the context (re-read from disk where it can be), and the answer
  passes through the same validator as a first-pass report.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from pathlib import Path
from typing import Callable

from .code_fixer import apply_unified_diff
from .code_tools import (
    complexity_snapshot,
    file_outline,
    find_symbol,
    run_project_tests,
    search_text,
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
Rules: only cite file paths and line numbers that appear in the provided FILE excerpts or \
error stack frames. Never invent paths, line numbers, compiler output, or test results. If evidence \
is insufficient, set confidence to "low", explain what is known vs uncertain, and list the next \
diagnostic step in needs_more_info. When you propose a fix, return a MINIMAL unified diff \
("--- a/path" / "+++ b/path" with @@ hunks) whose context lines match the real file exactly, \
so it can be applied directly; keep hunks small and scoped to the bug."""


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
    never fabricated.
    """
    if root is None:
        return
    for f in report.findings:
        if not f.verified:
            continue
        try:
            f.git_context = finding_git_context(root, f.file_path, f.start_line)
        except Exception:  # pragma: no cover - git_context is already defensive
            f.git_context = ""


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


def _read_frame_files(root: Path, error: ErrorInfo,
                      seen: set[Path], files: list[tuple[Path, str]],
                      read_notes: list[str], max_bytes: int,
                      cancel: threading.Event | None) -> None:
    """Read stack-trace frames first (strongest signal). Mutates in place."""
    for frame in error.frames[:8]:
        _check_cancelled(cancel)
        content, note = safe_read(root, frame.file, max_bytes=max_bytes)
        if content is not None:
            p = (root / frame.file).resolve() \
                if not Path(frame.file).is_absolute() else Path(frame.file)
            if p not in seen:
                files.append((p, content[:12000]))
                seen.add(p)
            read_notes.append(f"{frame.file}:{frame.line} ({note})")
        else:
            read_notes.append(f"{frame.file} — {note}")


def _read_candidate_files(root: Path, query: str,
                          seen: set[Path], files: list[tuple[Path, str]],
                          max_bytes: int,
                          cancel: threading.Event | None) -> None:
    """Read ranked candidates + one level of imports (bounded)."""
    for cand in list_candidate_files(root, query):
        _check_cancelled(cancel)
        if cand in seen:
            continue
        try:
            rel = cand.relative_to(root)
        except ValueError:
            continue
        content, _note = safe_read(root, rel, max_bytes=max_bytes)
        if content is not None:
            files.append((cand, content[:12000]))
            seen.add(cand)
            if len(files) <= 3:  # follow imports for the top hits only
                for related in follow_imports(cand, root):
                    if related in seen or len(files) >= 12:
                        break
                    if related.is_absolute():
                        target: str | Path = related
                    else:
                        target = related.relative_to(root) \
                            if _is_within(related, root) else related
                    extra, _ = safe_read(root, target, max_bytes=max_bytes)
                    if extra is not None:
                        files.append((related, extra[:12000]))
                        seen.add(related)
        if len(files) >= 12:
            break


def _code_intelligence_section(root: Path, error: ErrorInfo,
                               files: list[tuple[Path, str]]) -> str:
    """Bounded symbol/outline evidence so the model cites real definitions."""
    parts: list[str] = []
    try:
        keywords = [w for w in
                    re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}",
                               error.message or "")][:5]
        for kw in keywords[:3]:
            for hit in find_symbol(root, kw, limit=3):
                parts.append(f"symbol {kw}: {hit.path}:{hit.line}: {hit.snippet}")
                if len("\n".join(parts)) > 2000:
                    break
    except Exception:
        pass
    try:
        for path, content in files[:4]:
            try:
                outline = file_outline(path)[:12]
            except Exception:
                continue
            if outline:
                desc = ", ".join(f"{e.kind} {e.name}@{e.line}" for e in outline)
                try:
                    rel = path.relative_to(root)
                except ValueError:
                    rel = path
                parts.append(f"outline {rel}: {desc}")
                snap = complexity_snapshot(path, content)
                if snap.get("notes"):
                    parts.append(f"notes {rel}: {'; '.join(snap['notes'])}")
            if len("\n".join(parts)) > 3000:
                break
    except Exception:
        pass
    if not parts:
        return ""
    return "CODE INTELLIGENCE:\n" + "\n".join(parts)[:3500]


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


class InvestigationEngine:
    def __init__(self, config: TraceConfig | None = None,
                 ollama: OllamaClient | None = None):
        self.config = config or TraceConfig.from_env()
        self.ollama = ollama or OllamaClient(self.config)

    def investigate(self, request: InvestigationRequest,
                    progress_cb: ProgressCallback | None = None,
                    cancel_event: threading.Event | None = None,
                    investigation_id: str | None = None) -> InvestigationReport:
        inv_id = investigation_id or new_investigation_id()

        def emit(stage: str, message: str):
            idx = STAGES.index(stage) if stage in STAGES else 0
            progress = (idx + 1) / len(STAGES)
            if progress_cb:
                try:
                    progress_cb(ProgressEvent(inv_id, stage, message, progress))
                except Exception:
                    pass

        problems = request.validate()
        if problems:
            return InvestigationReport(
                investigation_id=inv_id, summary="Invalid investigation request.",
                root_cause="; ".join(problems), confidence="low",
                warnings=["Invalid request: " + "; ".join(problems)])

        # 1. receive
        _check_cancelled(cancel_event)
        emit("receive", "Received debugging request.")

        # 2. screenshot
        _check_cancelled(cancel_event)
        shot: ScreenshotResult | None = None
        screenshot_note = "screenshots disabled by request"
        screenshot_b64: str | None = None
        if request.include_screenshot:
            try:
                emit("screenshot", f"Capturing {request.screenshot_mode} screenshot…")
                shot = capture_screenshot(mode=request.screenshot_mode,
                                          max_dim=self.config.max_screenshot_dim)
                screenshot_b64 = base64.b64encode(shot.png_bytes).decode("ascii")
                screenshot_note = (f"screenshot captured via {shot.source} "
                                   f"({shot.width}x{shot.height}, {len(shot.png_bytes)} bytes)")
            except (ScreenshotCaptureError, ValueError) as e:
                hint = getattr(e, "hint", "")
                screenshot_note = f"screenshot unavailable: {e}. {hint}".strip()
                emit("screenshot", screenshot_note)
            except Exception as e:  # graceful
                screenshot_note = f"screenshot failed unexpectedly: {e}"
        else:
            emit("screenshot", screenshot_note)

        try:
            # 3. context
            _check_cancelled(cancel_event)
            emit("context", "Collecting local context…")
            ctx: LocalContext = collect_local_context(
                editor_selection=request.editor_selection,
                terminal_output=request.terminal_output,
                allow_clipboard=request.include_clipboard,
                project_hint=request.project_dir)

            # 4. project root
            _check_cancelled(cancel_event)
            emit("project_root", "Identifying project root…")
            root = find_project_root(request.project_dir or ctx.cwd)
            needs_more: list[str] = []
            if root is None:
                msg = ("Could not identify the active project. Select the project "
                       "folder or provide the missing context.")
                return InvestigationReport(
                    investigation_id=inv_id,
                    summary="Project root could not be identified.",
                    root_cause="Unknown project location; no files inspected.",
                    confidence="low", confidence_rationale=screenshot_note,
                    needs_more_info=[msg],
                    evidence_summary=f"{screenshot_note}\n{ctx.summary()}",
                    warnings=["No project files were read; refusing to invent an explanation."])

            # 5. error extraction
            _check_cancelled(cancel_event)
            emit("error_extract", "Extracting error message and stack trace…")
            combined_text = "\n\n".join(t for t in [
                request.request_text, request.terminal_output or "",
                request.editor_selection or "", request.extra_context or ""] if t)
            error: ErrorInfo = extract_error_info(combined_text)

            # 6. read files (frames first, then ranked candidates + imports)
            _check_cancelled(cancel_event)
            emit("read_files", f"Reading relevant files under {root}…")
            files: list[tuple[Path, str]] = []
            read_notes: list[str] = []
            seen: set[Path] = set()
            _read_frame_files(root, error, seen, files, read_notes,
                              self.config.max_file_bytes, cancel_event)
            _read_candidate_files(
                root, request.request_text + " " + error.message,
                seen, files, self.config.max_file_bytes, cancel_event)
            if not files:
                needs_more.append(
                    "No relevant project files could be identified. Select the project "
                    "folder, paste the error output, or name the file you suspect.")

            # 7. diagnostics (+ opt-in tests + code intelligence)
            _check_cancelled(cancel_event)
            emit("diagnostics", "Collecting compiler/linter/test diagnostics…")
            diagnostics = collect_diagnostics(
                root, [p for p, _ in files],
                timeout_s=self.config.diagnostics_timeout_s)
            _check_cancelled(cancel_event)
            intelligence = _code_intelligence_section(root, error, files)
            test_note = ""
            if request.run_tests:
                test_res = run_project_tests(
                    root, timeout_s=self.config.diagnostics_timeout_s,
                    allowed=self.config.allow_test_execution)
                if test_res.get("ran"):
                    test_note = (f"TESTS ({' '.join(test_res['command'])}): "
                                 f"{test_res.get('reason', '')}\n"
                                 f"{test_res.get('output', '')[:3000]}")
                else:
                    test_note = f"TESTS skipped: {test_res.get('reason', '')}"
                    needs_more.append(
                        "Test execution is opt-in (TRACE_ALLOW_TESTS=1); "
                        "enable it to include test output as evidence.")

            # 8. model analysis
            emit("model_analysis", "Thinking through the evidence…")
            bundle = build_model_context(
                request_text=request.request_text, error=error, files=files,
                diagnostics=diagnostics, local_summary=ctx.summary() + "\n" + screenshot_note,
                budget_chars=self.config.max_context_chars)
            if intelligence:
                bundle = (bundle + "\n\n---\n\n" + intelligence)[:self.config.max_context_chars]
            if test_note:
                bundle = (bundle + "\n\n---\n\n" + test_note)[:self.config.max_context_chars]
            user_prompt = (bundle + "\n\n---\n\nAnalyze the evidence above and return ONLY the JSON report. "
                           "Do not invent file paths or line numbers. "
                           "Propose a minimal unified diff that applies cleanly.")
            messages = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}]
            if screenshot_b64 and shot is not None:
                # Ollama vision: attach image to the user message when supported.
                messages[-1] = {"role": "user", "content": user_prompt,
                                "images": [screenshot_b64]}
            try:
                raw = self.ollama.chat(messages, stream=False,
                                       cancel_event=cancel_event,
                                       timeout=self.config.read_timeout_s)
            except OllamaCancelledError as e:
                raise InvestigationCancelled(str(e)) from e
            except OllamaError as e:
                hint = getattr(e, "hint", "")
                return InvestigationReport(
                    investigation_id=inv_id,
                    summary="Model analysis unavailable.",
                    root_cause=str(e), confidence="low",
                    confidence_rationale="No model verdict; evidence collected but not analyzed.",
                    needs_more_info=[hint] if hint else [],
                    evidence_summary=(f"{screenshot_note}\n{ctx.summary()}\n"
                                      f"Files read: {len(files)}. " + "; ".join(read_notes)[:2000]),
                    warnings=[f"Ollama error: {e}"])

            # 9. validate
            _check_cancelled(cancel_event)
            emit("validate", "Validating file paths and line numbers…")
            report = self._parse_report(inv_id, raw)
            # Enrich findings with verified excerpts dir context = root.
            report = sanitize_report(root, report)
            # Add grounded git "last changed" context to verified findings.
            _enrich_git_context(root, report)
            # 9b. auto-fix: go straight to the file and fix the bug when asked.
            if request.auto_fix:
                emit("validate", "Applying the verified fix…")
                report = _maybe_auto_fix(root, report, True,
                                         self.config.allow_file_edits)

            # 10. report
            emit("report", "Investigation complete.")
            if not report.evidence_summary:
                report.evidence_summary = (
                    f"{screenshot_note}\n{ctx.summary()}\n"
                    f"Project root: {root}\nFiles read: {len(files)}")
            if needs_more:
                report.needs_more_info = [*report.needs_more_info, *needs_more]
            if len(files) == 0:
                report.warnings.append(
                    "Screenshot/visual evidence alone is insufficient; no code excerpts "
                    "were available for verification.")
            return report
        finally:
            if shot is not None:
                dispose(shot)

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
            for f in (prior.get("findings") or [])[:6]:
                _check_cancelled(cancel_event)
                if not isinstance(f, dict):
                    continue
                rel = str(f.get("file_path", "")).strip()
                if not rel:
                    continue
                content, note = safe_read(root, rel,
                                          max_bytes=self.config.max_file_bytes)
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
                stream=False, cancel_event=cancel_event,
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
            findings = []
            for f in data.get("findings", []) or []:
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
            if isinstance(rec, dict) and (rec.get("description") or rec.get("diff")):
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
                alternative_hypotheses=[str(h)[:2000] for h in
                                        (data.get("alternative_hypotheses", []) or [])][:10],
                needs_more_info=[str(n)[:1000] for n in
                                 (data.get("needs_more_info", []) or [])][:10],
                evidence_summary=str(data.get("evidence_summary", ""))[:4000],
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
