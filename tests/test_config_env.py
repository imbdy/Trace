"""Tests: config knobs, .env loading rules, and the agent-iteration budget."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trace_backend.config import TraceConfig
from trace_backend.investigation_engine import InvestigationEngine
from trace_backend.schemas import InvestigationRequest


class TestConfigEnv(unittest.TestCase):
    def setUp(self):
        # Make sure repo-level config knobs never leak between tests.
        self._saved = {
            k: os.environ.get(k) for k in
            ("TRACE_MAX_ITERATIONS", "OLLAMA_HOST", "TRACE_MODEL",
             "TRACE_HISTORY", "TRACE_ALLOW_TESTS", "TRACE_ALLOW_FILE_EDITS")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_max_iterations_parsed_and_redacted(self):
        with mock.patch.dict(os.environ, {"TRACE_MAX_ITERATIONS": "37"}):
            cfg = TraceConfig.from_env()
        self.assertEqual(cfg.max_iterations, 37)
        self.assertEqual(cfg.redacted()["max_iterations"], 37)

    def test_max_iterations_out_of_range_fails_validation(self):
        base = TraceConfig()
        for bad in (1, 1001, -5):
            with self.subTest(bad=bad):
                problems = TraceConfig(max_iterations=bad).validate()
                self.assertTrue(any("max_iterations" in p for p in problems))
        self.assertEqual(base.validate(), [])
        self.assertEqual(base.max_iterations, 64)

    def test_dotenv_file_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "# comment\n"
                "TRACE_MAX_ITERATIONS = 55 \n"
                'TRACE_HISTORY="0"\n'
                "EMPTY=\n"
            )
            with mock.patch("trace_backend.config._candidate_env_files",
                            return_value=[env_file]):
                with mock.patch.dict(os.environ, {}, clear=False):
                    cfg = TraceConfig.from_env()
        self.assertEqual(cfg.max_iterations, 55)
        # Quoted value parsed, bare "0" off-flag recognised.
        self.assertFalse(cfg.history_enabled)

    def test_dotenv_never_overrides_process_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("TRACE_MAX_ITERATIONS=99\n")
            self._tok = None
            with mock.patch("trace_backend.config._candidate_env_files",
                            return_value=[env_file]):
                with mock.patch.dict(os.environ,
                                     {"TRACE_MAX_ITERATIONS": "41"}):
                    cfg = TraceConfig.from_env()
        self.assertEqual(cfg.max_iterations, 41)


class TestIterationBudget(unittest.TestCase):
    """TRACE_MAX_ITERATIONS actually drives the graph's cycle guard."""

    def setUp(self):
        import tempfile as _t
        self.tmp = _t.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "pyproject.toml").write_text("[project]\nname='x'\n")

    def test_tiny_budget_stops_the_run_honestly(self):
        ollama = _StubChat()
        engine = InvestigationEngine(TraceConfig(max_iterations=3), ollama=ollama)
        req = InvestigationRequest(request_text="debug me",
                                   project_dir=str(self.root),
                                   include_screenshot=False)
        with self.assertRaises(RuntimeError) as ctx:
            engine.investigate(req)
        self.assertIn("exceeded 3 steps", str(ctx.exception))


class _StubChat:
    def chat(self, messages, **kwargs):
        return "{}"


if __name__ == "__main__":
    unittest.main()
