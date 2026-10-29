"""Tests: screenshot failure is graceful; server is loopback-only; open-file validation;
provider connection probe (POST /api/v1/config/test)."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from trace_backend.config import TraceConfig
from trace_backend.investigation_engine import InvestigationEngine
from trace_backend.ollama_client import OllamaConnectionError
from trace_backend.schemas import InvestigationRequest
from trace_backend.screenshot_service import ScreenshotCaptureError
from trace_backend import investigation_engine as eng_mod
from trace_backend.server import PROBE_TIMEOUT_S, open_file_in_editor, run_server
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


class _ProbeOllama:
    """Probe-stub engine client: records the call, replies OK or raises."""

    def __init__(self, reply: str = "OK", exc: Exception | None = None):
        self.calls: list[dict] = []
        self._reply = reply
        self._exc = exc

    def probe(self, config: TraceConfig, timeout: float | None = None):
        self.calls.append({"config": config, "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        return self._reply

    def chat(self, messages, **kw):  # investigations never start here
        raise AssertionError("chat must not be called by the probe")


class _StubEngine:
    def __init__(self, ollama: _ProbeOllama):
        self.ollama = ollama


class TestConfigTestEndpoint(unittest.TestCase):
    """POST /api/v1/config/test — the settings panel's "Test connection"."""

    PORT = 18796

    def setUp(self):
        self.ollama = _ProbeOllama()
        self.config = TraceConfig(ollama_host="http://127.0.0.1:11434",
                                  ollama_model="saved-model",
                                  bind_host="127.0.0.1", bind_port=self.PORT)
        self.server = run_server(self.config, engine=_StubEngine(self.ollama),
                                 block=False)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        time.sleep(0.3)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _post(self, body: bytes):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.PORT}/api/v1/config/test",
            data=body, headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req)

    def test_success_reports_latency_and_persists_nothing(self):
        with self._post(json.dumps({
                "provider": "ollama", "model": "probe-model"}).encode()) as r:
            payload = json.loads(r.read())
        self.assertTrue(payload["ok"])
        self.assertIsInstance(payload["latency_ms"], int)
        self.assertGreaterEqual(payload["latency_ms"], 0)
        # Exactly one probe, against the body's model, bounded by PROBE_TIMEOUT_S.
        self.assertEqual(len(self.ollama.calls), 1)
        call = self.ollama.calls[0]
        self.assertEqual(call["config"].ollama_model, "probe-model")
        self.assertEqual(call["config"].provider, "ollama")
        self.assertEqual(call["timeout"], PROBE_TIMEOUT_S)
        # Untouched live config: nothing was saved by the test.
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.PORT}/api/v1/config") as r:
            cfg = json.loads(r.read())
        self.assertEqual(cfg["ollama_model"], "saved-model")

    def test_failure_is_an_honest_ok_false(self):
        self.ollama._exc = OllamaConnectionError(
            "Cannot reach Ollama at http://127.0.0.1:11434: refused.",
            hint="ollama serve")
        with self._post(json.dumps({
                "provider": "ollama", "model": "probe-model"}).encode()) as r:
            payload = json.loads(r.read())
        self.assertFalse(payload["ok"])
        self.assertIn("Cannot reach Ollama", payload["error"])

    def test_body_fields_fall_back_to_the_running_config(self):
        with self._post(json.dumps({}).encode()) as r:
            payload = json.loads(r.read())
        self.assertTrue(payload["ok"])
        call = self.ollama.calls[0]
        self.assertEqual(call["config"].ollama_model, "saved-model")
        self.assertEqual(call["config"].provider, "ollama")

    def test_invalid_body_is_bad_request(self):
        for body in ('{"provider": "weird", "model": "m"}',
                     '{"provider": "ollama", "model": ""}',
                     '{"provider": "openai-compatible", "model": "m", "api_base": "ftp://x"}'):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post(body.encode()).read()
            self.assertEqual(cm.exception.code, 400)
            detail = json.loads(cm.exception.read().decode())
            self.assertEqual(detail["error"]["code"], "bad_request")


if __name__ == "__main__":
    unittest.main()
