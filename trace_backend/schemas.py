"""Typed request/response schemas for the Trace backend ↔ frontend contract.

Plain dataclasses with JSON helpers (no third-party dependency) so the
contract stays usable from any frontend. See docs/API_CONTRACT.md.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field


def _now() -> float:
    return time.time()


@dataclass
class InvestigationRequest:
    request_text: str
    project_dir: str | None = None
    include_screenshot: bool = True
    screenshot_mode: str = "active"  # "active" | "full"
    include_clipboard: bool = False  # clipboard ONLY when explicitly True
    editor_selection: str | None = None
    terminal_output: str | None = None
    extra_context: str | None = None
    # When set, this request is a follow-up question about the investigation
    # named here: the engine reuses that report as context instead of
    # re-collecting evidence.
    follow_up_of: str | None = None
    # When True, the engine applies the verified fix diff straight to the
    # files on disk (with .trace-bak backups) instead of only proposing it.
    # Unverified diffs are never applied. The outcome lands in
    # InvestigationReport.fix_applied so the UI can stay quiet (no extra
    # window) and just report "fixed".
    auto_fix: bool = False
    # When True (and the server opted in via TRACE_ALLOW_TESTS=1), the engine
    # runs the project's allowlisted test command and includes the output as
    # evidence. Project scripts are never executed.
    run_tests: bool = False

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.request_text or not self.request_text.strip():
            problems.append("request_text must not be empty")
        if self.screenshot_mode not in ("active", "full"):
            problems.append("screenshot_mode must be 'active' or 'full'")
        if self.follow_up_of is not None and not str(self.follow_up_of).strip():
            problems.append("follow_up_of must be a non-empty investigation id")
        return problems

    @classmethod
    def from_dict(cls, data: dict) -> "InvestigationRequest":
        return cls(
            request_text=str(data.get("request_text", "")),
            project_dir=data.get("project_dir"),
            include_screenshot=bool(data.get("include_screenshot", True)),
            screenshot_mode=str(data.get("screenshot_mode", "active")),
            include_clipboard=bool(data.get("include_clipboard", False)),
            editor_selection=data.get("editor_selection"),
            terminal_output=data.get("terminal_output"),
            extra_context=data.get("extra_context"),
            follow_up_of=data.get("follow_up_of"),
            auto_fix=bool(data.get("auto_fix", False)),
            run_tests=bool(data.get("run_tests", False)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CodeExcerpt:
    path: str
    start_line: int
    end_line: int
    excerpt: str
    verified: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Finding:
    title: str
    file_path: str
    start_line: int
    end_line: int
    code_excerpt: str = ""
    explanation: str = ""
    evidence: str = ""
    confidence: str = "low"  # low | medium | high
    verified: bool = False
    # Grounded version-control context ("last changed in commit …"), populated
    # by the engine for verified findings only; empty when git is unavailable,
    # the dir is not a repo, or the location could not be verified.
    git_context: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RecommendedFix:
    description: str
    diff: str | None = None
    rationale: str = ""
    # True only when every hunk of ``diff`` was checked against the real file
    # on disk (validator.verify_fix_diff). False — the safe default — means the
    # patch is shown as-is and must not be trusted as applicable.
    diff_verified: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InvestigationReport:
    investigation_id: str
    summary: str
    root_cause: str
    confidence: str  # low | medium | high
    confidence_rationale: str = ""
    findings: list[Finding] = field(default_factory=list)
    recommended_fix: RecommendedFix | None = None
    alternative_hypotheses: list[str] = field(default_factory=list)
    needs_more_info: list[str] = field(default_factory=list)
    evidence_summary: str = ""
    warnings: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=_now)
    # Set when this report answers a follow-up question about another
    # investigation: the id of the investigation it continues. Empty for a
    # first-pass report.
    follow_up_of: str = ""
    # Populated when auto_fix applied (or attempted) the recommended diff:
    # {"applied": bool, "applied_files": [...], "backup_paths": [...],
    #  "warnings": [...], "dry_run": bool}. None when auto_fix was off.
    fix_applied: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class ProgressEvent:
    investigation_id: str
    stage: str
    message: str
    progress: float  # 0.0..1.0
    timestamp: float = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict())}\n\n"


@dataclass
class InvestigationStatus:
    investigation_id: str
    status: str  # running | done | error | cancelled
    progress: float = 0.0
    stage: str = ""
    report: dict | None = None
    error: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def new_investigation_id() -> str:
    return f"inv-{uuid.uuid4().hex[:12]}"


def error_envelope(code: str, message: str, hint: str = "") -> dict:
    return {"error": {"code": code, "message": message, "hint": hint}}


STAGES: list[str] = [
    "receive",
    "screenshot",
    "context",
    "project_root",
    "error_extract",
    "read_files",
    "diagnostics",
    "model_analysis",
    "validate",
    "report",
]
