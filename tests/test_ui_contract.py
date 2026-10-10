"""Tests: API client contract handling (mocked HTTP) + result view-model honesty."""

import io
import json
import queue
import threading
import unittest
from unittest import mock

from trace_ui import api_client as api_mod
from trace_ui.api_client import ApiError, TraceApiClient
from trace_ui.result_model import build_result_view, format_location


def _resp(payload: dict, status: int = 200):
    data = json.dumps(payload).encode()
    m = mock.MagicMock()
    m.read.return_value = data
    m.status = status
    m.__enter__.return_value = m
    m.__exit__.return_value = False
    return m


def _sse(lines: list[str]):
    blob = "".join(lines).encode()
    m = mock.MagicMock()
    m.readline.side_effect = io.BytesIO(blob).readline
    m.close.return_value = None
    return m


class TestApiClient(unittest.TestCase):
    def test_start_returns_id(self):
        with mock.patch.object(api_mod.urllib.request, "urlopen",
                               return_value=_resp({"investigation_id": "inv-1"},
                                                  202)):
            self.assertEqual(TraceApiClient().start_investigation({"request_text": "x"}),
                             "inv-1")

    def test_unreachable_backend_is_user_friendly(self):
        import urllib.error
        with mock.patch.object(api_mod.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(ApiError) as cm:
                TraceApiClient().health()
            self.assertEqual(cm.exception.code, "backend_unreachable")
            self.assertIn("main.py", cm.exception.hint)

    def test_http_error_envelope_parsed(self):
        import urllib.error
        err = urllib.error.HTTPError("http://x", 409, "Conflict", {},
                                     io.BytesIO(json.dumps(
                                         {"error": {"code": "bad_state",
                                                     "message": "already done"}}).encode()))
        with mock.patch.object(api_mod.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(ApiError) as cm:
                TraceApiClient().cancel_investigation("inv-9")
            self.assertEqual(cm.exception.code, "bad_state")

    def test_sse_forwards_real_events_and_stops_at_terminal(self):
        got: list = []
        ev1 = json.dumps({"investigation_id": "i", "stage": "read_files",
                          "message": "reading", "progress": 0.5})
        ev2 = json.dumps({"investigation_id": "i", "stage": "done",
                          "message": "complete", "progress": 1.0})
        fake = _sse([": keepalive\n\n", f"data: {ev1}\n\n",
                     "data: not-json\n\n", f"data: {ev2}\n\n"])
        with mock.patch.object(api_mod.urllib.request, "urlopen", return_value=fake):
            TraceApiClient().stream_events("i", got.append)
        stages = [e["stage"] for e in got]
        self.assertEqual(stages, ["read_files", "done"])  # malformed line skipped

    def test_sse_stop_event_aborts(self):
        stop = threading.Event()
        stop.set()
        fake = _sse(["data: {\"stage\": \"read_files\"}\n\n"])
        with mock.patch.object(api_mod.urllib.request, "urlopen", return_value=fake):
            TraceApiClient().stream_events("i", lambda e: None, stop=stop)

    def test_client_has_no_model_logic(self):
        import inspect
        src = inspect.getsource(api_mod)
        code_signals = ("import ollama", "from ollama", "OLLAMA_HOST",
                        "TRACE_MODEL", "/api/chat", "/api/generate",
                        "import neoma", "from neoma")
        for token in code_signals:
            self.assertNotIn(token, src)


class TestResultModel(unittest.TestCase):
    def _status(self, **kw):
        base = {"investigation_id": "inv-1", "status": "done", "progress": 1.0,
                "stage": "report",
                "report": {"investigation_id": "inv-1", "summary": "s",
                           "root_cause": "r", "confidence": "high",
                           "confidence_rationale": "why",
                           "findings": [
                               {"title": "Real bug", "file_path": "app.py",
                                "start_line": 4, "end_line": 5,
                                "code_excerpt": "    4: boom()",
                                "explanation": "e", "evidence": "tb",
                                "confidence": "high", "verified": True},
                               {"title": "Guess", "file_path": "ghost.py",
                                "start_line": 1, "end_line": 2,
                                "code_excerpt": "", "explanation": "maybe",
                                "evidence": "[UNVERIFIED: not found]",
                                "confidence": "low", "verified": False}],
                           "recommended_fix": {"description": "fix it",
                                               "diff": "--- a\n+++ b",
                                               "rationale": "because"},
                           "alternative_hypotheses": ["other"],
                           "needs_more_info": [], "evidence_summary": "ev",
                           "warnings": ["w1"]}}
        base.update(kw)
        return base

    def test_verified_and_hypotheses_split(self):
        view = build_result_view(self._status())
        self.assertTrue(view.complete)
        self.assertEqual(len(view.verified_findings), 1)
        self.assertEqual(len(view.hypotheses), 1)
        self.assertIn("UNVERIFIED", view.hypotheses[0].verification_note)
        self.assertIn("Verified", view.verified_findings[0].verification_note)

    def test_cancelled_is_banner_not_results(self):
        view = build_result_view({"status": "cancelled"})
        self.assertFalse(view.complete)
        self.assertIn("cancelled", view.banner.lower())
        self.assertEqual(view.findings, [])

    def test_error_status_surfaced(self):
        view = build_result_view({"status": "error",
                                  "error": {"message": "boom failed"}})
        self.assertFalse(view.complete)
        self.assertIn("boom failed", view.banner)

    def test_garbage_payload_never_crashes(self):
        view = build_result_view({"status": "done", "report": "nonsense"})
        self.assertFalse(view.complete)

    def test_location_format(self):
        self.assertEqual(format_location("a.py", 4, 5), "a.py:4–5")
        self.assertEqual(format_location("a.py", 4, 4), "a.py:4")
        self.assertEqual(format_location("", 0, 0), "(no file reported)")


class TestNoForbiddenIntegrations(unittest.TestCase):
    def test_no_neoma_integration_in_ui_or_backend(self):
        """Fail on code-level Neoma integration (imports, clients, URLs).
        Prose mentions stating independence (e.g. docstrings) are allowed."""
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent
        signal = re.compile(
            r"^\s*(import|from)\s+neoma|neoma\s*\.\s*\w|neoma[-_]?(api|client|session)|"
            r"https?://\S*neoma", re.IGNORECASE | re.MULTILINE)
        hits = []
        for sub in ("trace_ui", "trace_backend", "main.py"):
            p = root / sub
            files = [p] if p.is_file() else list(p.rglob("*.py"))
            for f in files:
                try:
                    text = f.read_text(encoding="utf-8")
                except OSError:
                    continue
                if signal.search(text):
                    hits.append(str(f))
        self.assertEqual(hits, [], f"forbidden integration refs: {hits}")


if __name__ == "__main__":
    unittest.main()
