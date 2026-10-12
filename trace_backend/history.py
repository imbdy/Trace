"""Investigation history: an on-disk journal so a completed verdict survives a
process restart and can be recalled — or followed up on — without re-running the
(possibly cloud) model.

Storage is one small JSON file per investigation inside a single directory, so a
``get`` is a single read and pruning by age/count is trivial. Writes are atomic
(temp file + ``os.replace``) and every failure is swallowed: history is a
convenience, never something that can break an investigation. Stdlib only — this
module must not import any other Trace component (see tests/test_layering.py).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


def default_history_dir() -> str:
    """Return the default history directory (per-user, OS-appropriate)."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") \
        or os.path.expanduser("~")
    return str(Path(base) / "Trace" / "history")


def _summary(payload: dict) -> dict:
    """A trimmed, honest view of a stored status for list/recall UIs."""
    rep = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    req = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    findings = rep.get("findings") or []
    findings = [f for f in findings if isinstance(f, dict)]
    verified = sum(1 for f in findings if f.get("verified"))
    return {
        "investigation_id": payload.get("investigation_id", ""),
        "status": payload.get("status", ""),
        "created_at": rep.get("created_at") or payload.get("created_at") or 0,
        "summary": str(rep.get("summary", "") or "")[:300],
        "confidence": rep.get("confidence") or "",
        "finding_count": len(findings),
        "verified_count": verified,
        "project_dir": str(req.get("project_dir", "") or ""),
        "request_text_preview": str(req.get("request_text_preview", "") or "")[:300],
        # Report first (it is the record of truth), then the request, so a
        # follow-up that errored before producing a report still shows its link.
        "follow_up_of": str(rep.get("follow_up_of") or req.get("follow_up_of") or ""),
    }


class HistoryJournal:
    """Append/recall store for finished investigations. Best-effort and safe."""

    def __init__(self, directory: str, max_entries: int = 500):
        self.dir = Path(directory)
        self.max_entries = max(1, int(max_entries))
        self.lock = threading.Lock()
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # read-only FS etc.: every op below already tolerates absence

    def _path(self, inv_id: str) -> Path:
        # Confine to the history dir: never let an id escape it.
        safe = "".join(c for c in str(inv_id) if c.isalnum() or c in ("-", "_"))
        return self.dir / f"{safe or 'unknown'}.json"

    def record(self, inv_id: str, payload: dict) -> None:
        """Persist (or overwrite) the status for ``inv_id``. Never raises."""
        if not inv_id:
            return
        p = self._path(inv_id)
        tmp = p.with_suffix(".tmp")
        try:
            with self.lock:
                tmp.write_text(json.dumps(payload, ensure_ascii=False),
                               encoding="utf-8")
                os.replace(tmp, p)
            self._prune()
        except OSError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def get(self, inv_id: str) -> dict | None:
        p = self._path(inv_id)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def list(self, limit: int = 50) -> list[dict]:
        """Newest-first summaries, capped at ``limit``."""
        try:
            files = list(self.dir.glob("*.json"))
        except OSError:
            return []
        summaries: list[dict] = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # a corrupt entry never breaks the whole list
            if isinstance(data, dict):
                summaries.append(_summary(data))
        summaries.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
        return summaries[: max(0, int(limit))]

    def _prune(self) -> None:
        """Keep only the newest ``max_entries`` files (by mtime)."""
        try:
            files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        excess = len(files) - self.max_entries
        for f in files[: max(0, excess)]:
            try:
                f.unlink()
            except OSError:
                pass
