"""Safe file fixing: apply a verified unified diff to the project on disk.

Safety contract (mirrors project_inspector / validator):

- Only files inside the detected project ``root`` are ever touched.
  Anything escaping the root is skipped with a warning, never written.
- Only diffs whose old-side (context + removed lines) matches the real file
  are applied. Unverified hunks are skipped, never force-applied.
- Every overwritten file gets a ``<name>.trace-bak`` backup first (unless
  ``create_backup=False``), written atomically (temp file + ``os.replace``).
- ``dry_run=True`` verifies without writing anything.
- Never raises into the caller: all failures become warnings.

Layering: imports only ``project_inspector`` (root/excludes), ``validator``
(diff parsing + grounding) and ``schemas`` types. Never touches the model,
the server, or any UI code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .project_inspector import is_excluded, resolve_within_root
from .validator import _old_side_matches, _parse_diff_hunks


@dataclass
class FixResult:
    ok: bool = False
    applied: bool = False
    dry_run: bool = False
    applied_files: list[str] = field(default_factory=list)
    backup_paths: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "applied": self.applied,
            "dry_run": self.dry_run,
            "applied_files": list(self.applied_files),
            "backup_paths": list(self.backup_paths),
            "skipped": list(self.skipped),
            "warnings": list(self.warnings),
        }


def _read_lines(path: Path) -> tuple[list[str], bool]:
    """Return (lines, ends_with_newline). Never raises (caller handles)."""
    raw = path.read_bytes().decode("utf-8", errors="replace")
    ends = raw.endswith("\n")
    return raw.splitlines(), ends


def _backup_path(path: Path) -> Path:
    return path.parent / (path.name + ".trace-bak")


def apply_unified_diff(
    root: Path | None,
    diff: str | None,
    *,
    dry_run: bool = False,
    create_backup: bool = True,
) -> FixResult:
    """Apply ``diff`` to files under ``root``. Never raises."""
    res = FixResult(dry_run=bool(dry_run))
    if root is None:
        res.warnings.append("No project root: refusing to edit files.")
        return res
    if not diff or not diff.strip():
        res.warnings.append("No diff to apply.")
        return res
    try:
        hunks = _parse_diff_hunks(diff)
    except Exception:
        res.warnings.append("Could not parse the diff; nothing was changed.")
        return res
    if not hunks:
        res.warnings.append("No applicable hunks found in the diff.")
        return res

    # Group hunks per target file, preserving order.
    by_file: dict[str, list[dict]] = {}
    for h in hunks:
        path = (h.get("path") or "").strip()
        if not path or path == "/dev/null":
            res.skipped.append(path or "(no path)")
            continue
        by_file.setdefault(path, []).append(h)

    if not by_file:
        res.warnings.append("Diff targets no real files; nothing was changed.")
        return res

    try:
        resolved_root = root.resolve()
    except OSError:
        res.warnings.append(f"Project root is unreadable: {root}")
        return res

    overall_ok = True
    for rel, file_hunks in by_file.items():
        resolved = resolve_within_root(root, rel)
        if resolved is None:
            overall_ok = False
            msg = f"Refused to edit {rel}: outside project root."
            res.warnings.append(msg)
            res.skipped.append(rel)
            continue
        try:
            rel_to_root = resolved.relative_to(resolved_root)
        except ValueError:
            if resolved != resolved_root:
                overall_ok = False
                res.warnings.append(f"Refused to edit {rel}: outside project root.")
                res.skipped.append(rel)
                continue
            rel_to_root = Path(".")
        if is_excluded(rel_to_root):
            overall_ok = False
            res.warnings.append(f"Refused to edit {rel}: excluded directory.")
            res.skipped.append(rel)
            continue

        exists = resolved.is_file()
        if not exists and any(h.get("old") for h in file_hunks):
            # Hunks expect existing content but the file is missing.
            overall_ok = False
            res.warnings.append(f"Could not verify {rel}: file not found.")
            res.skipped.append(rel)
            continue

        # Verify every hunk's old-side against disk before touching anything.
        file_ok = True
        for h in file_hunks:
            old = h.get("old") or []
            start = int(h.get("old_start") or 0)
            if not old:
                continue  # pure insertion: line anchor checked below
            if start < 1 or not _old_side_matches(root, rel, start, old):
                file_ok = False
                break
            # Anchor line itself must exist for pure insertions too.
        if not file_ok:
            overall_ok = False
            res.warnings.append(
                f"The suggested fix could not be verified against {rel} "
                "and was not applied.")
            res.skipped.append(rel)
            continue
        # Pure-insertion anchors: start must be within [1, len+1].
        if exists:
            try:
                current, _ = _read_lines(resolved)
            except OSError as e:
                overall_ok = False
                res.warnings.append(f"Could not read {rel}: {e}")
                res.skipped.append(rel)
                continue
            for h in file_hunks:
                if not (h.get("old") or []):
                    start = int(h.get("old_start") or 0)
                    if start < 1 or start > len(current) + 1:
                        file_ok = False
                        break
            if not file_ok:
                overall_ok = False
                res.warnings.append(
                    f"The suggested fix could not be verified against {rel} "
                    "and was not applied.")
                res.skipped.append(rel)
                continue

        if res.dry_run:
            res.applied_files.append(rel)
            continue

        try:
            if exists:
                lines, ends_nl = _read_lines(resolved)
            else:
                lines, ends_nl = [], True
            # Apply bottom-up so earlier line numbers stay valid.
            ordered = sorted(file_hunks,
                             key=lambda h: int(h.get("old_start") or 0),
                             reverse=True)
            for h in ordered:
                old = h.get("old") or []
                new = h.get("new") or []
                start = int(h.get("old_start") or 0)
                idx = max(0, start - 1)
                if not old:
                    # Pure insertion before line ``start`` (start may be len+1).
                    idx = min(max(0, start - 1), len(lines))
                    lines[idx:idx] = list(new)
                else:
                    lines[idx:idx + len(old)] = list(new)
            text = "\n".join(lines) + ("\n" if (lines and ends_nl) else "")
            if create_backup and exists:
                bak = _backup_path(resolved)
                try:
                    bak.write_bytes(resolved.read_bytes())
                    res.backup_paths.append(str(bak))
                except OSError as e:
                    overall_ok = False
                    res.warnings.append(f"Backup failed for {rel}: {e}")
                    res.skipped.append(rel)
                    continue
            if not exists:
                try:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    overall_ok = False
                    res.warnings.append(f"Could not create {rel}: {e}")
                    res.skipped.append(rel)
                    continue
            tmp = resolved.parent / (resolved.name + ".trace-tmp")
            try:
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, resolved)
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
            res.applied_files.append(rel)
        except OSError as e:
            overall_ok = False
            res.warnings.append(f"Could not write {rel}: {e}")
            res.skipped.append(rel)
        except Exception as e:  # pragma: no cover - defensive
            overall_ok = False
            res.warnings.append(f"Could not apply fix to {rel}: {e}")
            res.skipped.append(rel)

    res.ok = overall_ok or bool(res.applied_files)
    res.applied = bool(res.applied_files) and not res.dry_run
    if res.dry_run:
        res.applied = False
    return res


def apply_report_fix(
    root: Path | None,
    report: dict,
    *,
    dry_run: bool = False,
    create_backup: bool = True,
) -> FixResult:
    """Apply ``report['recommended_fix']['diff']``. Never raises."""
    try:
        fix = (report or {}).get("recommended_fix") or {}
        diff = fix.get("diff") if isinstance(fix, dict) else None
    except Exception:
        diff = None
    if not diff:
        res = FixResult(dry_run=bool(dry_run))
        res.warnings.append("Report has no fix diff to apply.")
        return res
    return apply_unified_diff(root, diff, dry_run=dry_run,
                              create_backup=create_backup)
