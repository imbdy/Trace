"""Tests: the history journal, recall after restart, and the follow-up loop.

The journal itself is dict-based and stdlib-only, so these tests drive it both
directly and through the HTTP surface (a brand-new server instance standing in
for a process restart).
"""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from trace_backend.config import TraceConfig
from trace_backend.history import HistoryJournal
from trace_backend.investigation_engine import InvestigationEngine
from trace_backend.schemas import InvestigationRequest
from trace_backend.server import InvestigationStore, run_server

FIRST_JSON = ('{"summary": "boom(None) crashes", "root_cause": "x is None", '
              '"confidence": "medium", "confidence_rationale": "traceback", '
              '"findings": [{"title": "unguarded strip", "file_path": "app.py", '
              '"start_line": 1, "end_line": 2, "explanation": "x.strip() on None", '
              '"evidence": "traceback"}], "needs_more_info": []}')

FOLLOW_UP_JSON = ('{"summary": "Yes: guard the None case", '
                  '"root_cause": "boom(None) reaches x.strip()", '
                  '"confidence": "medium", "confidence_rationale": "prior report", '
                  '"findings": [{"title": "unguarded strip", "file_path": "app.py", '
                  '"start_line": 2, "end_line": 2, '
                  '"explanation": "the strip call is the crash site", '
                  '"evidence": "traceback"}], "needs_more_info": []}')


class StubOllama:
    """Minimal stub matching OllamaClient.chat (first ask, then follow-up)."""

    def __init__(self, first=FIRST_JSON, follow_up=FOLLOW_UP_JSON):
        self.responses = [first, follow_up]
        self.calls = 0

    def chat(self, messages, **kw):
        i = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[i]


def _payload(inv_id: str, *, created_at: float = 0.0, follow_up_of: str = "") -> dict:
    return {
        "investigation_id": inv_id,
        "status": "done",
        "created_at": created_at,
        "report": {
            "investigation_id": inv_id,
            "summary": f"summary of {inv_id}",
            "confidence": "medium",
            "created_at": created_at,
            "follow_up_of": follow_up_of,
            "findings": [
                {"title": "t", "file_path": "app.py", "start_line": 1,
                 "end_line": 2, "verified": True},
                {"title": "u", "file_path": "gone.py", "start_line": 1,
                 "end_line": 1, "verified": False},
            ],
        },
        "request": {"request_text_preview": "why does it crash",
                    "project_dir": "C:/code/app", "follow_up_of": follow_up_of},
    }


class TestHistoryJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "history"
        self.journal = HistoryJournal(str(self.dir), max_entries=3)

    def test_record_and_get_round_trip(self):
        self.journal.record("inv-1", _payload("inv-1"))
        self.assertEqual([p.name for p in self.dir.glob("*.json")], ["inv-1.json"])
        self.assertEqual(self.journal.get("inv-1")["status"], "done")

    def test_unknown_id_is_none(self):
        self.assertIsNone(self.journal.get("inv-nope"))

    def test_list_is_newest_first_and_summarised(self):
        self.journal.record("inv-old", _payload("inv-old", created_at=1000.0))
        self.journal.record("inv-new", _payload("inv-new", created_at=2000.0,
                                               follow_up_of="inv-old"))
        entries = self.journal.list(limit=10)
        self.assertEqual([e["investigation_id"] for e in entries],
                         ["inv-new", "inv-old"])
        new = entries[0]
        self.assertEqual(new["status"], "done")
        self.assertEqual(new["summary"], "summary of inv-new")
        self.assertEqual(new["project_dir"], "C:/code/app")
        self.assertEqual(new["request_text_preview"], "why does it crash")
        self.assertEqual(new["verified_count"], 1)
        self.assertEqual(new["follow_up_of"], "inv-old")
        self.assertEqual(self.journal.list(limit=1), [new])

    def test_prune_keeps_only_newest(self):
        for i in range(5):
            self.journal.record(f"inv-{i}", _payload(f"inv-{i}", created_at=1000.0 + i))
            time.sleep(0.01)  # mtime granularity on Windows
        self.assertEqual(len(list(self.dir.glob("*.json"))), 3)
        self.assertEqual(len(self.journal.list(limit=50)), 3)
        self.assertIsNone(self.journal.get("inv-0"))
        self.assertIsNone(self.journal.get("inv-1"))
        self.assertIsNotNone(self.journal.get("inv-4"))

    def test_corrupt_entry_never_breaks_list_or_get(self):
        self.journal.record("inv-good", _payload("inv-good", created_at=10.0))
        self.journal.dir.joinpath("inv-broken.json").write_text("{not json",
                                                                encoding="utf-8")
        entries = self.journal.list(limit=10)
        self.assertEqual([e["investigation_id"] for e in entries], ["inv-good"])
        self.assertIsNone(self.journal.get("inv-broken"))

    def test_id_cannot_escape_the_history_dir(self):
        self.journal.record("../../escape", _payload("../../escape"))
        self.assertEqual([p.name for p in self.dir.glob("*.json")], ["escape.json"])

    def test_missing_directory_is_tolerated(self):
        gone = HistoryJournal(str(Path(self.tmp.name) / "nope" / "deeper"),
                              max_entries=2)
        self.assertEqual(gone.list(limit=10), [])
        self.assertIsNone(gone.get("inv-1"))
        gone.record("inv-1", _payload("inv-1"))  # must not raise

class TestHistoryRecallOverHttp(unittest.TestCase):
    """Start an investigation, then read it back from a fresh server."""

    def _config(self, port: int, history_dir: Path) -> TraceConfig:
        return TraceConfig(bind_host="127.0.0.1", bind_port=port,
                           ollama_host="http://127.0.0.1:11434",
                           history_dir=str(history_dir), history_max_entries=50)

    def _serve(self, config: TraceConfig, engine: InvestigationEngine):
        server = run_server(config, engine=engine, block=False)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        # LIFO: shutdown the loop first, then close the socket.
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def _stop(self, server) -> None:
        server.shutdown()
        server.server_close()

    def _get(self, port: int, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, json.loads(r.read())

    def _post(self, port: int, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())

    def _wait_done(self, port: int, inv_id: str) -> dict:
        for _ in range(150):
            _, status = self._get(port, f"/api/v1/investigations/{inv_id}")
            if status["status"] in ("done", "error", "cancelled"):
                return status
            time.sleep(0.1)
        self.fail(f"{inv_id} never finished")

    def test_follow_up_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname='h'\n")
            (root / "app.py").write_text(
                "def boom(x):\n    return x.strip()\n\n\nboom(None)\n")
            history_dir = root / "history"
            config = self._config(18790, history_dir)
            engine = InvestigationEngine(config, ollama=StubOllama())  # type: ignore[arg-type]
            self._serve(config, engine)
            code, started = self._post(18790, "/api/v1/investigations",
                                       {"request_text": "why does it crash",
                                        "project_dir": str(root),
                                        "include_screenshot": False})
            self.assertEqual(code, 202)
            parent_id = started["investigation_id"]
            done = self._wait_done(18790, parent_id)
            self.assertEqual(done["status"], "done")
            self.assertTrue(done["report"]["findings"][0]["verified"])

            # The history list carries the fields a UI needs.
            _, listing = self._get(18790, "/api/v1/investigations?limit=10")
            self.assertEqual(listing["count"], 1)
            entry = listing["investigations"][0]
            self.assertEqual(entry["investigation_id"], parent_id)
            self.assertEqual(entry["verified_count"], 1)
            self.assertEqual(entry["project_dir"], str(root))
            self.assertEqual(entry["request_text_preview"], "why does it crash")
            self.assertEqual(entry["follow_up_of"], "")

            # A follow-up is answered from the live verdict, links back, and is
            # itself verified against disk.
            code, child = self._post(
                18790, f"/api/v1/investigations/{parent_id}/follow-up",
                {"request_text": "which line crashes?"})
            self.assertEqual(code, 202)
            self.assertNotEqual(child["investigation_id"], parent_id)
            self.assertEqual(child["follow_up_of"], parent_id)
            child_done = self._wait_done(18790, child["investigation_id"])
            self.assertEqual(child_done["status"], "done")
            report = child_done["report"]
            self.assertEqual(report["follow_up_of"], parent_id)
            self.assertEqual(report["findings"][0]["file_path"], "app.py")
            self.assertTrue(report["findings"][0]["verified"])

            _, listing = self._get(18790, "/api/v1/investigations?limit=10")
            self.assertEqual(listing["count"], 2)
            linked = [e for e in listing["investigations"]
                      if e["investigation_id"] == child["investigation_id"]][0]
            self.assertEqual(linked["follow_up_of"], parent_id)

            # Simulate a restart: a brand-new store on the same history dir.
            fresh = InvestigationStore(
                InvestigationEngine(config, ollama=StubOllama()), config)  # type: ignore[arg-type]
            self.assertIsNone(fresh.get(parent_id), "fresh store must be empty")
            self.assertEqual(fresh.recall(parent_id).report["root_cause"], "x is None")
            self.assertEqual(
                fresh.recall(child["investigation_id"]).report["follow_up_of"],
                parent_id)
            self.assertIsNone(fresh.recall("inv-does-not-exist"))


    def test_unknown_follow_up_is_404(self):
        with tempfile.TemporaryDirectory() as td:
            config = self._config(18791, Path(td) / "history")
            engine = InvestigationEngine(config, ollama=StubOllama())  # type: ignore[arg-type]
            self._serve(config, engine)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post(18791, "/api/v1/investigations/inv-nope/follow-up",
                           {"request_text": "hello?"})
            self.assertEqual(cm.exception.code, 404)
            self.assertEqual(json.loads(cm.exception.read())["error"]["code"],
                             "not_found")

    def test_follow_up_without_request_text_is_400(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname='h'\n")
            config = self._config(18792, root / "history")
            engine = InvestigationEngine(config, ollama=StubOllama())  # type: ignore[arg-type]
            self._serve(config, engine)
            code, started = self._post(18792, "/api/v1/investigations",
                                       {"request_text": "why", "project_dir": str(root),
                                        "include_screenshot": False})
            self.assertEqual(code, 202)
            parent_id = started["investigation_id"]
            self._wait_done(18792, parent_id)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post(18792,
                           f"/api/v1/investigations/{parent_id}/follow-up",
                           {"request_text": "   "})
            self.assertEqual(cm.exception.code, 400)

    def test_follow_up_of_in_the_start_body_is_honoured(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname='h'\n")
            (root / "app.py").write_text("def boom(x):\n    return x.strip()\n")
            config = self._config(18794, root / "history")
            engine = InvestigationEngine(config, ollama=StubOllama())  # type: ignore[arg-type]
            self._serve(config, engine)
            code, started = self._post(18794, "/api/v1/investigations",
                                       {"request_text": "why", "project_dir": str(root),
                                        "include_screenshot": False})
            self.assertEqual(code, 202)
            parent_id = started["investigation_id"]
            self._wait_done(18794, parent_id)
            # An unknown parent is refused, never silently downgraded to a
            # first-pass investigation.
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post(18794, "/api/v1/investigations",
                           {"request_text": "and?", "follow_up_of": "inv-ghost",
                            "project_dir": str(root), "include_screenshot": False})
            self.assertEqual(cm.exception.code, 404)
            # A known parent produces a linked follow-up.
            code, child = self._post(18794, "/api/v1/investigations",
                                     {"request_text": "and now?",
                                      "follow_up_of": parent_id,
                                      "project_dir": str(root),
                                      "include_screenshot": False})
            self.assertEqual(code, 202)
            child_done = self._wait_done(18794, child["investigation_id"])
            self.assertEqual(child_done["report"]["follow_up_of"], parent_id)

    def test_recall_after_restart_over_http(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname='h'\n")
            (root / "app.py").write_text("def boom(x):\n    return x.strip()\n")
            config = self._config(18793, root / "history")
            engine = InvestigationEngine(config, ollama=StubOllama())  # type: ignore[arg-type]
            first = self._serve(config, engine)
            code, started = self._post(18793, "/api/v1/investigations",
                                       {"request_text": "why", "project_dir": str(root),
                                        "include_screenshot": False})
            self.assertEqual(code, 202)
            parent_id = started["investigation_id"]
            self._wait_done(18793, parent_id)

            # A real restart: stop the first process, then serve again on the
            # same history dir with a brand-new (empty) store.
            self._stop(first)
            self._serve(config, InvestigationEngine(config, ollama=StubOllama()))  # type: ignore[arg-type]
            status, recalled = self._get(18793,
                                         f"/api/v1/investigations/{parent_id}")
            self.assertEqual(status, 200)
            self.assertEqual(recalled["status"], "done")
            self.assertEqual(recalled["report"]["investigation_id"], parent_id)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._get(18793, "/api/v1/investigations/inv-never-recorded")
            self.assertEqual(cm.exception.code, 404)

    def test_history_disabled_store_recalls_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            config = TraceConfig(history_enabled=False,
                                 history_dir=str(Path(td) / "history"))
            store = InvestigationStore(InvestigationEngine(config), config)
            self.assertIsNone(store.history)
            self.assertEqual(store.list_history(), [])
            self.assertIsNone(store.recall("inv-1"))
            self.assertEqual(store.request_context("inv-1"), {})


class _RunningJobStub:
    """A job that exists but has no verdict yet."""

    def __init__(self):
        self.status = "running"
        self.report = None


class TestStoreHistoryUnit(unittest.TestCase):
    """Store-level wiring without HTTP."""

    def _store(self, root: Path) -> InvestigationStore:
        config = TraceConfig(history_dir=str(root / "history"))
        return InvestigationStore(
            InvestigationEngine(config, ollama=StubOllama()), config)  # type: ignore[arg-type]

    def test_finished_job_is_recorded_and_summarised(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname='h'\n")
            (root / "app.py").write_text("def boom(x):\n    return x.strip()\n")
            store = self._store(root)
            job = store.start(InvestigationRequest(
                request_text="why does it crash", project_dir=str(root),
                include_screenshot=False))
            job.thread.join(timeout=20)
            self.assertEqual(job.status, "done")
            entries = store.list_history(limit=10)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["investigation_id"], job.id)
            self.assertEqual(entries[0]["project_dir"], str(root))
            self.assertEqual(store.recall(job.id).report, job.report)
            # The in-memory job wins over the journal.
            self.assertEqual(store.resolve_report(job.id), job.report)

    def test_resolve_report_ignores_a_running_job(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store(Path(td))
            store.jobs["inv-parent"] = _RunningJobStub()
            self.assertIsNone(store.resolve_report("inv-parent"))
            self.assertIsNone(store.resolve_report("inv-unknown"))

    def test_request_context_falls_back_to_the_journal(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store(Path(td))
            store.history.record("inv-gone", _payload("inv-gone", created_at=5.0))
            ctx = store.request_context("inv-gone")
            self.assertEqual(ctx["project_dir"], "C:/code/app")
            self.assertEqual(ctx["request_text_preview"], "why does it crash")


if __name__ == "__main__":
    unittest.main()

