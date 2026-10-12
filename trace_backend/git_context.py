"""Best-effort version-control context for a finding's location.

Given the project root and a file path, ask git when that code was last
touched and by whom. This is advisory enrichment only — never a substitute
for disk verification: if git is missing, the directory is not a repository,
or the path escapes the root, this returns an empty string and adds nothing.
It never fabricates a commit.

Conventions mirror ``project_inspector``: a ``shutil.which("git")`` guard,
``subprocess.run`` with a timeout and ``cwd=root``, truncated output, and
``resolve_within_root`` so a model-supplied path can never point outside the
project. Every call is wrapped so it never raises into the pipeline.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .project_inspector import resolve_within_root


def _git(args: list[str], cwd: Path, timeout_s: float) -> str:
    """Run a git command; return stdout on success, "" on any failure."""
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return ""
    if proc.returncode != 0:
        return ""  # not a repo, bad args, path has no history, …
    return (proc.stdout or "").strip()[-4000:]


def finding_git_context(root: Path | None, file_path: str,
                        line: int | None = None,
                        timeout_s: float = 10.0) -> str:
    """Return a grounded "last changed in commit <sha> by <author> <time> ago".

    ``line`` is accepted for a line-scoped query in the future; today git's
    ``%ar`` (relative time) is file-scoped, which is the honest granularity we
    can report without a line-level blame parse. Empty string on any failure —
    the caller treats "" as "no VC info available" rather than inventing one.
    """
    if root is None or not file_path or not file_path.strip():
        return ""
    if shutil.which("git") is None:
        return ""
    resolved = resolve_within_root(root, file_path)
    if resolved is None:
        return ""  # path escapes the project root
    try:
        rel = resolved.relative_to(root.resolve())
    except ValueError:
        return ""
    out = _git(["log", "-n", "1", "--format=%h|%an|%ar", "--", str(rel)],
               root, timeout_s)
    if not out:
        return ""
    parts = out.splitlines()[0].strip().split("|")
    if len(parts) < 3:
        return ""
    sha, author, rel_time = parts[0].strip(), parts[1].strip(), "|".join(parts[2:]).strip()
    if not sha:
        return ""
    return f"last changed in commit {sha} by {author} {rel_time}".strip()
