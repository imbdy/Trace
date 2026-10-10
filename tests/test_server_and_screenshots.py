"""Tests: screenshot failure is graceful; server is loopback-only; open-file validation."""

import json
import threading
import time
import unittest
import urllib.request

from trace_backend.config import TraceConfig
from trace_backend.investigation_engine import InvestigationEngine
from trace_backend.schemas import InvestigationRequest
from trace_backend.screenshot_service import ScreenshotCaptureError
from trace_backend import investigation_engine as eng_mod
from trace_backend.server import open_file_in_editor, run_server
from unittest import mock


class TestScreenshotGraceful(unittest.TestCase):
    def test_capture_failure_does_not_kill_pipeline(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname='z'\n")
            (root / "a.py").write_text("x = 1\n")

            class StubOllama:
                def chat(self, messages, **kw):
                    return '{"summary": "s", "root_cause": "r", "confidence": "low"}'

            engine = InvestigationEngine(TraceConfig(), ollama=StubOllama())  # type: ignore[arg-type]
            req = InvestigationRequest(request_text="help", project_dir=str(root),
                                       include_screenshot=True)
            with mock.patch.object(eng_mod, "capture_screenshot",
                                   side_effect=ScreenshotCaptureError(
                                       "no backend", hint="pip install mss pillow")), \
                 mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]):
                report = engine.investigate(req)
            self.assertEqual(report.confidence, "low")
            self.assertIn("screenshot", report.evidence_summary.lower())


class TestServer(unittest.TestCase):
    def test_loopback_binding_and_flow(self):
        config = TraceConfig(bind_host="127.0.0.1", bind_port=18777)

        class StubOllama:
            def chat(self, messages, **kw):
                return '{"summary": "s", "root_cause": "r", "confidence": "low"}'

        engine = InvestigationEngine(config, ollama=StubOllama())  # type: ignore[arg-type]
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pyproject.toml").write_text("[project]\nname='q'\n")
            server = run_server(config, engine=engine, block=False)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                time.sleep(0.3)
                # health
                with urllib.request.urlopen("http://127.0.0.1:18777/api/v1/health") as r:
                    self.assertEqual(json.loads(r.read())["ok"], True)
                # start investigation
                payload = json.dumps({"request_text": "hi", "project_dir": td,
                                      "include_screenshot": False}).encode()
                req = urllib.request.Request(
                    "http://127.0.0.1:18777/api/v1/investigations",
                    data=payload, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req) as r:
                    inv_id = json.loads(r.read())["investigation_id"]
                # poll for completion
                for _ in range(100):
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:18777/api/v1/investigations/{inv_id}") as r:
                        status = json.loads(r.read())
                    if status["status"] in ("done", "error", "cancelled"):
                        break
                    time.sleep(0.1)
                self.assertIn(status["status"], ("done", "error", "cancelled"))
            finally:
                server.shutdown()
                server.server_close()

    def test_non_loopback_bind_rejected(self):
        config = TraceConfig(bind_host="0.0.0.0", bind_port=18778)
        with self.assertRaises(ValueError):
            run_server(config, block=False)

    def test_open_missing_file(self):
        res = open_file_in_editor("/nonexistent-trace-xyz/file.py")
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
