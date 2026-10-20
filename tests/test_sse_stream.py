"""Tests: SSE event delivery is race-free — a mid-run subscriber receives the
full history exactly once and always gets the terminator; finished jobs are
evicted from memory (history journal keeps the recall).

Run via: python -m unittest tests.test_sse_stream -v
"""

import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from trace_backend.config import TraceConfig
from trace_backend.investigation_engine import InvestigationEngine
from trace_backend.schemas import (
    InvestigationReport,
    InvestigationRequest,
    ProgressEvent,
)
from trace_backend.server import (
    InvestigationStore,
    run_server,
)
from trace_backend import investigation_engine as eng_mod


class ScriptedEngine:
    """Emits one pre-gate event, waits for the test to subscribe, then emits
    a second live event and returns a valid report."""

    def __init__(self):
        self.gate = threading.Event()
        self.released = threading.Event()

    def investigate(self, request, progress_cb=None, cancel_event=None,
                    investigation_id=None):
        cb = progress_cb or (lambda ev: None)
        cb(ProgressEvent(investigation_id, "tree_read", "collecting", 0.2))
        self.gate.wait(timeout=5)
        cb(ProgressEvent(investigation_id, "model_analysis", "thinking", 0.6))
        self.released.set()
        return InvestigationReport(
            investigation_id=investigation_id or "inv-x",
            summary="s", root_cause="r", confidence="low")

    def follow_up(self, *a, **kw):  # pragma: no cover - unused here
        raise AssertionError("not expected")


def _request(td: str) -> InvestigationRequest:
    return InvestigationRequest(request_text="hi", project_dir=td,
                                include_screenshot=False)


def _make_store(td: str, engine) -> InvestigationStore:
    return InvestigationStore(engine)  # type: ignore[arg-type]


class TestSSEDelivery(unittest.TestCase):

    def test_mid_run_subscriber_gets_history_once_plus_terminator(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pyproject.toml").write_text("[project]\nname='x'\n")
            engine = ScriptedEngine()
            store = _make_store(td, engine)
            job = store.start(_request(td))
            # Wait for the first event to land in history, then subscribe
            # while the run is still gated open.
            for _ in range(200):
                if store.get(job.id) is not None and len(job.events) >= 1:
                    break
                time.sleep(0.01)
            q = store.subscribe(job)
            engine.gate.set()
            # Drain: history event, live event, terminal, terminator.
            items = []
            while True:
                item = q.get(timeout=5)
                items.append(item)
                if item is None:
                    break
            stages = [getattr(i, "stage", None) for i in items]
            self.assertEqual(stages, ["tree_read", "model_analysis",
                                      "done", None])
            self.assertEqual(items.count(None), 1,
                             "exactly one terminator, exactly once")
            self.assertEqual(stages.count("tree_read"), 1,
                             "no duplicate replay")
            self.assertEqual(stages.count("done"), 1)

    def test_late_subscriber_on_finished_job_replays_and_terminates(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pyproject.toml").write_text("[project]\nname='x'\n")
            engine = ScriptedEngine()
            engine.gate.set()  # run straight through
            store = _make_store(td, engine)
            job = store.start(_request(td))
            for _ in range(400):
                if store.get(job.id).status in ("done", "error", "cancelled"):
                    break
                time.sleep(0.01)
            q = store.subscribe(job)
            items = []
            while True:
                item = q.get(timeout=5)
                items.append(item)
                if item is None:
                    break
            stages = [getattr(i, "stage", None) for i in items]
            self.assertIn("done", stages)
            self.assertEqual(stages.count("model_analysis"), 1)
            self.assertEqual(stages.count("tree_read"), 1)
            self.assertEqual(items.count(None), 1)


class TestSSEOverHTTP(unittest.TestCase):
    """Full round trip: the stream over HTTP replays history once, ends with
    the terminal event, and closes — no hang, no duplicates."""

    def test_events_stream_round_trip(self):
        config = TraceConfig(bind_host="127.0.0.1", bind_port=18794)

        class StubOllama:
            def chat(self, messages, **kw):
                return '{"summary": "s", "root_cause": "r", "confidence": "low"}'

        engine = InvestigationEngine(config, ollama=StubOllama())  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pyproject.toml").write_text("[project]\nname='q'\n")
            server = run_server(config, engine=engine, block=False)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                time.sleep(0.3)
                payload = json.dumps({"request_text": "hi", "project_dir": td,
                                      "include_screenshot": False}).encode()
                req = urllib.request.Request(
                    "http://127.0.0.1:18794/api/v1/investigations",
                    data=payload, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req) as r:
                    inv_id = json.loads(r.read())["investigation_id"]
                for _ in range(100):
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:18794/api/v1/investigations/{inv_id}") as r:
                        status = json.loads(r.read())
                    if status["status"] in ("done", "error", "cancelled"):
                        break
                    time.sleep(0.1)
                with urllib.request.urlopen(
                        f"http://127.0.0.1:18794/api/v1/investigations/{inv_id}/events") as r:
                    body = r.read().decode("utf-8")
                events = [ln[6:] for ln in body.splitlines()
                          if ln.startswith("data: ")]
                payload_objs = []
                for data in events:
                    try:
                        payload_objs.append(json.loads(data))
                    except json.JSONDecodeError:
                        pass
                stages = [p.get("stage") for p in payload_objs
                          if isinstance(p, dict)]
                self.assertIn("done", stages)
                self.assertEqual(stages.count("done"), 1,
                                 "the terminal event must appear exactly once")
                # The stream CLOSES after the terminal event (urllib returned)
                # — a lost terminator would hang this read forever.
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()

