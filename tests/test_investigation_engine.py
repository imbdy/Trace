"""Tests: cancellation, missing project, screenshot cleanup, inspector safety."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from trace_backend import investigation_engine as eng_mod
from trace_backend.config import TraceConfig
from trace_backend.investigation_engine import InvestigationCancelled, InvestigationEngine
from trace_backend.project_inspector import (
    find_project_root,
    redact_secrets,
    resolve_within_root,
    safe_read,
)
from trace_backend.schemas import InvestigationRequest, InvestigationReport
from trace_backend.screenshot_service import ScreenshotResult, dispose
from trace_backend.validator import sanitize_report


def _cfg():
    return TraceConfig(ollama_host="http://127.0.0.1:11434",
                       ollama_model="gemma4:31b-cloud")


class FakeOllama:
    """Minimal stub matching OllamaClient.chat signature."""

    def __init__(self, response: str = "", delay: float = 0.0):
        self.response = response
        self.delay = delay
        self.calls = 0
        self.last_messages: list = []

    def chat(self, messages, **kwargs):
        from trace_backend.ollama_client import OllamaCancelledError
        self.calls += 1
        self.last_messages = messages
        cancel = kwargs.get("cancel_event")
        if self.delay:
            deadline = time.time() + self.delay
            while time.time() < deadline:
                if cancel is not None and cancel.is_set():
                    raise OllamaCancelledError("cancelled")
                time.sleep(0.02)
        if cancel is not None and cancel.is_set():
            raise OllamaCancelledError("cancelled")
        return self.response


GOOD_JSON = ('{"summary": "Null deref", "root_cause": "x is None", '
             '"confidence": "medium", "confidence_rationale": "traceback", '
             '"findings": [{"title": "boom", "file_path": "app.py", '
             '"start_line": 1, "end_line": 2, "explanation": "e", "evidence": "tb"}], '
             '"recommended_fix": {"description": "guard None"}, '
             '"alternative_hypotheses": ["other"], "needs_more_info": [], '
             '"evidence_summary": "ev"}')


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "pyproject.toml").write_text("[project]\nname='x'\n")
        (self.root / "app.py").write_text(
            "def boom(x):\n    return x.strip()\n\n\nboom(None)\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _engine(self, response=GOOD_JSON, delay=0.0,
                screenshot: ScreenshotResult | None = None):
        ollama = FakeOllama(response, delay)
        engine = InvestigationEngine(_cfg(), ollama=ollama)  # type: ignore[arg-type]
        return engine, ollama

    def test_full_pipeline_verifies_paths(self):
        engine, _ = self._engine()
        req = InvestigationRequest(
            request_text="AttributeError in app.py",
            project_dir=str(self.root), include_screenshot=False,
            terminal_output='Traceback (most recent call last):\n  File "app.py", line 4, in <module>\n    boom(None)\nAttributeError: NoneType')
        with mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]):
            report = engine.investigate(req)
        self.assertIsInstance(report, InvestigationReport)
        self.assertTrue(report.findings)
        # app.py finding verified against disk.
        app_findings = [f for f in report.findings if f.file_path == "app.py"]
        self.assertTrue(app_findings and app_findings[0].verified)
        self.assertIn("def boom", app_findings[0].code_excerpt)

    def test_explain_mode_skips_error_extraction(self):
        engine, ollama = self._engine()
        with mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]), \
                mock.patch.object(eng_mod, "extract_error_info") as mock_extract:
            req = InvestigationRequest(
                request_text="How does app.py work?",
                project_dir=str(self.root), include_screenshot=False,
                investigation_type="explain")
            report = engine.investigate(req)
        # Error extraction must not be called in explain mode.
        mock_extract.assert_not_called()
        self.assertIsInstance(report, InvestigationReport)
        # The model was called with the explain system prompt, not the debug one.
        self.assertEqual(ollama.calls, 1)
        system_msg = ollama.last_messages[0]
        self.assertEqual(system_msg["role"], "system")
        self.assertIn("code-explanation assistant", system_msg["content"])
        self.assertNotIn("unified diff", system_msg["content"])

    def test_explain_mode_does_not_run_tests(self):
        engine, _ = self._engine()
        with mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]), \
                mock.patch.object(eng_mod, "run_project_tests") as mock_tests:
            req = InvestigationRequest(
                request_text="Explain the code in app.py",
                project_dir=str(self.root), include_screenshot=False,
                investigation_type="explain", run_tests=True)
            report = engine.investigate(req)
        # run_tests must be ignored in explain mode.
        mock_tests.assert_not_called()
        self.assertIsInstance(report, InvestigationReport)

    def test_explain_mode_reads_files_and_reports(self):
        engine, ollama = self._engine()
        with mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]):
            req = InvestigationRequest(
                request_text="Explain what boom() does in app.py",
                project_dir=str(self.root), include_screenshot=False,
                investigation_type="explain")
            report = engine.investigate(req)
        self.assertIsInstance(report, InvestigationReport)
        # The model was called with the explain system prompt.
        self.assertEqual(ollama.calls, 1)
        system_msg = ollama.last_messages[0]
        self.assertEqual(system_msg["role"], "system")
        self.assertIn("code-explanation assistant", system_msg["content"])
        self.assertNotIn("unified diff", system_msg["content"])

    def test_cancellation_before_model(self):
        engine, _ = self._engine(delay=2.0)
        req = InvestigationRequest(request_text="hang?", project_dir=str(self.root),
                                   include_screenshot=False)
        cancel = threading.Event()
        errors: list = []

        def run():
            try:
                with mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]):
                    engine.investigate(req, cancel_event=cancel)
            except InvestigationCancelled as e:
                errors.append(e)

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.2)
        cancel.set()
        t.join(timeout=10)
        self.assertTrue(errors, "expected InvestigationCancelled")

    def test_missing_files_yields_honest_report(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        eroot = Path(empty.name)
        # Detectable root (.git marker) but zero readable source files:
        # only a binary blob, which the allowlist skips.
        (eroot / ".git").mkdir()
        (eroot / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        engine, _ = self._engine(response='{"summary":"?","root_cause":"?","confidence":"low"}')
        req = InvestigationRequest(request_text="something broke",
                                   project_dir=str(eroot),
                                   include_screenshot=False)
        with mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]):
            report = engine.investigate(req)
        self.assertEqual(report.confidence, "low")
        self.assertTrue(report.needs_more_info)

    def test_screenshot_temp_file_cleaned_up(self):
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        import os
        fd_path = str(self.root / "shot.png")
        with open(fd_path, "wb") as f:
            f.write(png)
        shot = ScreenshotResult(width=10, height=10, source="test",
                                png_bytes=png, temp_path=fd_path)
        engine, _ = self._engine()
        req = InvestigationRequest(request_text="x", project_dir=str(self.root),
                                   include_screenshot=True)
        with mock.patch.object(eng_mod, "capture_screenshot", return_value=shot), \
             mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]):
            engine.investigate(req)
        self.assertFalse(os.path.exists(fd_path), "temp screenshot must be deleted")
        self.assertIsNone(shot.temp_path)

    def test_dispose_removes_file(self):
        fd_path = str(self.root / "t.png")
        Path(fd_path).write_bytes(b"data")
        r = ScreenshotResult(width=1, height=1, source="t",
                             png_bytes=b"data", temp_path=fd_path)
        dispose(r)
        self.assertFalse(Path(fd_path).exists())
        self.assertIsNone(r.temp_path)


class TestFollowUp(unittest.TestCase):
    """Feature 2: asking a follow-up question about a prior verdict."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "pyproject.toml").write_text("[project]\nname='x'\n")
        (self.root / "app.py").write_text(
            "def boom(x):\n    return x.strip()\n\n\nboom(None)\n")

    def _engine(self, response=GOOD_JSON, delay: float = 0.0):
        ollama = FakeOllama(response, delay)
        return InvestigationEngine(_cfg(), ollama=ollama), ollama

    def _request(self, **kw):
        base = dict(request_text="which line crashes?",
                    project_dir=str(self.root), include_screenshot=False,
                    follow_up_of="inv-parent")
        base.update(kw)
        return InvestigationRequest(**base)

    def _prior(self) -> InvestigationReport:
        rep = InvestigationEngine._parse_report("inv-parent", GOOD_JSON)
        return sanitize_report(self.root, rep)

    def test_follow_up_reuses_prior_report_and_links_parent(self):
        engine, ollama = self._engine()
        prior = self._prior()
        stages: list[str] = []
        report = engine.follow_up(self._request(), prior,
                                  lambda stage, message: stages.append(stage),
                                  None, investigation_id="inv-child")
        self.assertEqual(report.investigation_id, "inv-child")
        self.assertEqual(report.follow_up_of, "inv-parent")
        self.assertEqual(report.to_dict()["follow_up_of"], "inv-parent")
        # Findings are validated against disk, exactly like a first-pass run.
        self.assertTrue(report.findings[0].verified)
        self.assertTrue(report.findings[0].code_excerpt)
        self.assertEqual(stages[0], "receive")
        self.assertIn("model_analysis", stages)
        self.assertEqual(stages[-1], "report")
        # The prior verdict was actually sent to the model as context.
        prompt = ollama.last_messages[-1]["content"]
        self.assertIn("Null deref", prompt)
        self.assertIn("inv-parent", prompt)
        # The cited file was re-read from disk with real line numbers.
        self.assertIn("def boom(x):", prompt)
        self.assertIn("   1: def boom(x):", prompt)

    def test_follow_up_accepts_a_recalled_report_dict(self):
        engine, ollama = self._engine()
        prior = self._prior().to_dict()
        report = engine.follow_up(self._request(), prior, None, None,
                                  investigation_id="inv-child")
        self.assertEqual(report.follow_up_of, "inv-parent")
        self.assertIn("inv-parent", ollama.last_messages[-1]["content"])

    def test_follow_up_without_project_dir_is_honest(self):
        engine, _ = self._engine()
        report = engine.follow_up(self._request(project_dir=None), self._prior(),
                                  None, None, investigation_id="inv-child")
        self.assertEqual(report.follow_up_of, "inv-parent")
        self.assertTrue(any("Project root" in n for n in report.needs_more_info))
        # Locations could not be checked, so they are labelled, not dropped.
        self.assertFalse(report.findings[0].verified)
        self.assertIn("[UNVERIFIED", report.findings[0].evidence)

    def test_follow_up_without_a_prior_report_says_so(self):
        engine, _ = self._engine()
        report = engine.follow_up(self._request(), None, None, None,
                                  investigation_id="inv-child")
        self.assertTrue(any("No prior report" in w for w in report.warnings))

    def test_follow_up_validates_the_request(self):
        engine, ollama = self._engine()
        report = engine.follow_up(self._request(request_text="  "), self._prior(),
                                  None, None, investigation_id="inv-child")
        self.assertEqual(report.confidence, "low")
        self.assertEqual(ollama.calls, 0)
        self.assertTrue(any("request_text" in w for w in report.warnings))

    def test_follow_up_can_be_cancelled(self):
        engine, _ = self._engine(delay=0.5)
        cancel = threading.Event()
        errors: list[Exception] = []

        def run():
            try:
                engine.follow_up(self._request(), self._prior(), None, cancel,
                                 investigation_id="inv-child")
            except Exception as e:  # noqa: BLE001 - test records the failure
                errors.append(e)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.2)
        cancel.set()
        t.join(timeout=10)
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], InvestigationCancelled)



class TestInspectorSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".git").mkdir()
        (self.root / "app.py").write_text("print(1)\n")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "evil.py").write_text("print(2)\n")
        (self.root / "secret.py").write_text("API_KEY = 'sk-abcdef123456'\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_root_via_git(self):
        sub = self.root / "pkg"
        sub.mkdir()
        self.assertEqual(find_project_root(sub), self.root.resolve())

    def test_traversal_rejected(self):
        self.assertIsNone(resolve_within_root(self.root, "../escape.py"))

    def test_excluded_dir_rejected(self):
        content, note = safe_read(self.root, ".venv/evil.py")
        self.assertIsNone(content)
        self.assertIn("excluded", note)

    def test_secrets_redacted(self):
        out = redact_secrets("API_KEY = 'sk-abcdef1234567890' and password: hunter2")
        self.assertNotIn("sk-abcdef1234567890", out)
        self.assertIn("REDACTED", out)


if __name__ == "__main__":
    unittest.main()
