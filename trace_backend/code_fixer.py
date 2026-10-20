"""Safe file fixing: apply a verified unified diff to the project on disk.

Safety contract (mirrors project_inspector / validator):

- Only files inside the detected project ``root`` are ever touched.
  Anything escaping the root is skipped with a warning, never written.
- Only diffs whose old-side (context + removed lines) matches the real file
  are applied. Unverified hunks are skipped, never force-applied.
- Every overwritten file gets a ``<name>.trace-bak`` backup first (unless
  ``create_backup=False``), written atomically (temp file + ``os.replace``).
- The fixer does not silently transcode files: line endings and encoding of
  each file are detected and preserved (CRLF stays CRLF; a non-UTF-8 file is
  round-tripped byte-for-byte via latin-1 instead of being rewritten with
  U+FFFD replacement characters).
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


def _read_text(path: Path) -> tuple[list[str], bool, str, str]:
    """Return (lines, ends_with_newline, line_ending, encoding).

    Preserves the file's own format instead of silently transcoding it:
    - encoding is UTF-8, UTF-8 with BOM, or (lossless byte round-trip)
      latin-1 for non-standard-UTF-8 files — never ``errors="replace"``,
      which permanently turned unknown bytes into U+FFFD on save;
    - ``line_ending`` is "\n" or "\r\n", detected from the raw bytes, so a
      CRLF file does not come back with rewritten endings.
    Never raises (caller handles).
    """
    data = path.read_bytes()
    enc = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        raw = data.decode(enc)
    except UnicodeDecodeError:
        # latin-1 maps every byte; encode->decode round-trips losslessly.
        enc = "latin-1"
        raw = data.decode(enc)
    ends = raw.endswith("\n")
    return raw.splitlines(), ends, ("\r\n" if b"\r\n" in data else "\n"), enc


def _read_lines(path: Path) -> tuple[list[str], bool]:
    """Return (lines, ends_with_newline). Never raises (caller handles)."""
    lines, ends, _eol, _enc = _read_text(path)
    return lines, ends


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
                lines, ends_nl, eol, enc = _read_text(resolved)
            else:
                # Brand-new file: plain UTF-8, platform-neutral LF.
                lines, ends_nl, eol, enc = [], True, "\n", "utf-8"
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
            # Re-join with the file's OWN line ending, not a bolted-on LF.
            text = eol.join(lines) + (eol if (lines and ends_nl) else "")
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
                # Save in the file's ORIGINAL encoding (and BOM state), so a
                # fix never silently rewrites a whole file's bytes.
                tmp.write_bytes(text.encode(enc))
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
