"""Tests: safe auto-fix writes only verified diffs, with backups."""

import tempfile
import unittest
from pathlib import Path

from trace_backend.code_fixer import apply_report_fix, apply_unified_diff
from trace_backend.config import TraceConfig
from trace_backend.investigation_engine import InvestigationEngine
from trace_backend.schemas import InvestigationRequest


GOOD_DIFF = (
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -3,2 +3,3 @@\n"
    " def boom(x):\n"
    "-    return x.strip()\n"
    "+    if x is None:\n"
    "+        return None\n"
    "+    return x.strip()\n"
)


def _project() -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "app.py").write_text(
        "import os\n\ndef boom(x):\n    return x.strip()\n\n\nboom(None)\n",
        encoding="utf-8")
    return tmp, root


class TestApplyDiff(unittest.TestCase):
    def test_verified_diff_applies_with_backup(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        res = apply_unified_diff(root, GOOD_DIFF)
        self.assertTrue(res.applied)
        self.assertEqual(res.applied_files, ["app.py"])
        self.assertTrue((root / "app.py.trace-bak").is_file())
        self.assertIn("if x is None", (root / "app.py").read_text())

    def test_dry_run_changes_nothing(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        before = (root / "app.py").read_text()
        res = apply_unified_diff(root, GOOD_DIFF, dry_run=True)
        self.assertFalse(res.applied)
        self.assertEqual((root / "app.py").read_text(), before)

    def test_unverified_context_is_refused(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        bad = ("--- a/app.py\n+++ b/app.py\n@@ -3,1 +3,2 @@\n"
               " this line is not in the file\n+    added\n")
        before = (root / "app.py").read_text()
        res = apply_unified_diff(root, bad)
        self.assertFalse(res.applied)
        self.assertEqual((root / "app.py").read_text(), before)
        self.assertTrue(res.warnings)

    def test_path_escape_is_refused(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        evil = ("--- a/../escape.py\n+++ b/../escape.py\n@@ -1,1 +1,2 @@\n"
                " x\n+    y\n")
        res = apply_unified_diff(root, evil)
        self.assertFalse(res.applied)
        self.assertTrue(any("outside" in w for w in res.warnings))

    def test_empty_diff_warns_without_writing(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        res = apply_unified_diff(root, "   ")
        self.assertFalse(res.applied)


def _replace_line_diff(lineno: int, old: str, new: str) -> str:
    return (f"--- a/app.py\n+++ b/app.py\n"
            f"@@ -{lineno},1 +{lineno},1 @@\n-{old}\n+{new}\n")



def _replace_line_diff(lineno: int, old: str, new: str) -> str:
    return (f"--- a/app.py\n+++ b/app.py\n"
            f"@@ -{lineno},1 +{lineno},1 @@\n-{old}\n+{new}\n")


class TestBackupRotation(unittest.TestCase):
    """A second fix must never destroy the backup of the first one."""

    def test_true_original_survives_repeated_fixes(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        # LF on purpose: write_bytes guarantees exact line endings,
        # write_text may translate newlines per platform.
        (root / "app.py").write_bytes(b"L1\nL2\n")
        true_original = b"L1\nL2\n"

        res1 = apply_unified_diff(root, _replace_line_diff(2, "L2", "L2 fixed"))
        self.assertTrue(res1.applied)
        res2 = apply_unified_diff(root,
                                  _replace_line_diff(2, "L2 fixed", "L2 fixed again"))
        self.assertTrue(res2.applied)
        res3 = apply_unified_diff(root,
                                  _replace_line_diff(2, "L2 fixed again", "L2 final"))
        self.assertTrue(res3.applied)

        # .trace-bak-1 holds the true original forever, never clobbered by a
        # later fix; .trace-bak holds the state right before the last fix.
        first_bak = root / "app.py.trace-bak-1"
        self.assertTrue(first_bak.is_file(),
                        "a repeated fix must rotate the previous backup")
        self.assertEqual(first_bak.read_bytes(), true_original)
        self.assertEqual((root / "app.py.trace-bak").read_bytes(),
                         b"L1\nL2 fixed again\n")
        self.assertIn("L2 final", (root / "app.py").read_text())


class TestFormatPreservation(unittest.TestCase):
    """The fixer must not transcode encodings or flip line endings."""

    def test_crlf_and_latin1_bytes_survive(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        # "café" in cp1252 bytes (0xe9); NOT valid UTF-8.
        data = b"caf\xe9 = 1\r\nx = 2\r\nx = 3\r\n"
        (root / "app.py").write_bytes(data)
        diff = _replace_line_diff(2, "x = 2", "x = 3")
        res = apply_unified_diff(root, diff)
        self.assertTrue(res.applied)
        out = (root / "app.py").read_bytes()
        self.assertIn(b"caf\xe9", out,
                      "non-UTF-8 bytes must round-trip byte-for-byte")
        self.assertNotIn(b"\xef\xbf\xbd", out,
                         "no U+FFFD replacement chars may be written")
        self.assertIn(b"x = 3\r\n", out, "CRLF must stay CRLF")
        self.assertLessEqual(out.count(b"\n"), out.count(b"\r\n"))

    def test_utf8_bom_survives(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        (root / "app.py").write_bytes(b"\xef\xbb\xbfmarker = 1\nx = 2\n")
        diff = _replace_line_diff(2, "x = 2", "x = 3")
        res = apply_unified_diff(root, diff)
        self.assertTrue(res.applied)
        out = (root / "app.py").read_bytes()
        self.assertTrue(out.startswith(b"\xef\xbb\xbf"),
                        "the BOM must be preserved, not dropped or doubled")
        self.assertIn(b"marker = 1\nx = 3\n", out)


class TestEngineAutoFix(unittest.TestCase):
    def test_auto_fix_writes_verified_diff(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)

        class Stub:
            def chat(self, messages, **kw):
                import json
                return json.dumps({
                    "summary": "None crash", "root_cause": "x is None",
                    "confidence": "high",
                    "findings": [{"title": "boom", "file_path": "app.py",
                                  "start_line": 3, "end_line": 4,
                                  "explanation": "e", "evidence": "tb"}],
                    "recommended_fix": {"description": "guard None",
                                        "diff": GOOD_DIFF},
                })

        engine = InvestigationEngine(TraceConfig(), ollama=Stub())  # type: ignore[arg-type]
        req = InvestigationRequest(request_text="crash in app.py",
                                   project_dir=str(root),
                                   include_screenshot=False, auto_fix=True)
        from unittest import mock
        from trace_backend import investigation_engine as eng_mod
        with mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]):
            report = engine.investigate(req)
        self.assertIsNotNone(report.fix_applied)
        self.assertTrue(report.fix_applied["applied"])
        self.assertIn("if x is None", (root / "app.py").read_text())

    def test_auto_fix_skips_unverified_diff(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)

        class Stub:
            def chat(self, messages, **kw):
                import json
                return json.dumps({
                    "summary": "s", "root_cause": "r", "confidence": "high",
                    "findings": [{"title": "t", "file_path": "ghost.py",
                                  "start_line": 1, "end_line": 2}],
                    "recommended_fix": {"description": "d",
                                        "diff": "--- a/ghost.py\n+++ b/ghost.py\n"
                                                "@@ -1,1 +1,2 @@\n x\n+y\n"},
                })

        engine = InvestigationEngine(TraceConfig(), ollama=Stub())  # type: ignore[arg-type]
        req = InvestigationRequest(request_text="x", project_dir=str(root),
                                   include_screenshot=False, auto_fix=True)
        from unittest import mock
        from trace_backend import investigation_engine as eng_mod
        with mock.patch.object(eng_mod, "collect_diagnostics", return_value=[]):
            report = engine.investigate(req)
        self.assertFalse((report.fix_applied or {}).get("applied", False))
        # Project untouched apart from the original files.
        self.assertFalse((root / "ghost.py").exists())

    def test_engine_apply_fix_dry_run(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        engine = InvestigationEngine(TraceConfig())
        before = (root / "app.py").read_text()
        out = engine.apply_fix(
            {"recommended_fix": {"description": "d", "diff": GOOD_DIFF,
                                 "diff_verified": True}},
            str(root), dry_run=True)
        self.assertTrue(out.get("ok"))
        self.assertEqual((root / "app.py").read_text(), before)

    def test_apply_report_fix_helper(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        res = apply_report_fix(
            root, {"recommended_fix": {"diff": GOOD_DIFF}})
        self.assertTrue(res.applied)


class TestApplyFixHttp(unittest.TestCase):
    def _serve(self, root: Path, port: int):
        import threading
        from trace_backend.server import run_server
        import json

        class Stub:
            def chat(self, messages, **kw):
                return json.dumps({
                    "summary": "s", "root_cause": "r", "confidence": "high",
                    "findings": [{"title": "t", "file_path": "app.py",
                                  "start_line": 3, "end_line": 4}],
                    "recommended_fix": {"description": "guard",
                                        "diff": GOOD_DIFF}})

        config = TraceConfig(bind_host="127.0.0.1", bind_port=port)
        engine = InvestigationEngine(config, ollama=Stub())  # type: ignore[arg-type]
        server = run_server(config, engine=engine, block=False)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def _post(self, port: int, path: str, body: dict):
        import json
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())

    def test_apply_fix_endpoint_writes_file(self):
        import time
        import urllib.request
        import json
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        self._serve(root, 18795)
        _, started = self._post(18795, "/api/v1/investigations",
                                {"request_text": "fix it",
                                 "project_dir": str(root),
                                 "include_screenshot": False})
        inv = started["investigation_id"]
        for _ in range(150):
            with urllib.request.urlopen(
                    f"http://127.0.0.1:18795/api/v1/investigations/{inv}") as r:
                st = json.loads(r.read())
            if st["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        self.assertEqual(st["status"], "done")
        code, out = self._post(18795, f"/api/v1/investigations/{inv}/apply-fix",
                               {"dry_run": False})
        self.assertEqual(code, 200)
        self.assertTrue(out.get("applied"))
        self.assertIn("if x is None", (root / "app.py").read_text())

    def test_apply_fix_unknown_id_is_404(self):
        import urllib.error
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        self._serve(root, 18796)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post(18796, "/api/v1/investigations/inv-nope/apply-fix", {})
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()



