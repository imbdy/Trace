"""Tests: code intelligence helpers (search, outline, tests)."""

import tempfile
import unittest
from pathlib import Path

from trace_backend import code_tools as tools


def _project() -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "app.py").write_text(
        "def boom(x):\n    return x.strip()\n\n\nclass Handler:\n"
        "    def handle(self):\n        return boom(None)\n",
        encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "evil.py").write_text("def boom():\n    pass\n")
    return tmp, root


class TestCodeTools(unittest.TestCase):
    def test_search_finds_real_file_not_venv(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        hits = tools.search_text(root, "boom")
        paths = {h.path for h in hits}
        self.assertIn("app.py", paths)
        self.assertNotIn(".venv/evil.py", paths)

    def test_outline_lists_defs(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        names = {e.name for e in tools.file_outline(root / "app.py")}
        self.assertIn("boom", names)
        self.assertIn("Handler", names)

    def test_find_symbol_prefers_definitions(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        hits = tools.find_symbol(root, "boom")
        self.assertTrue(hits)
        self.assertIn("boom", hits[0].snippet)

    def test_complexity_snapshot(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        snap = tools.complexity_snapshot(root / "app.py")
        self.assertGreater(snap["loc"], 0)

    def test_tests_are_opt_in(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        off = tools.run_project_tests(root, allowed=False)
        self.assertFalse(off["ran"])
        # Even when allowed, this helper never raises.
        on = tools.run_project_tests(root, timeout_s=5, allowed=True)
        self.assertIn("ran", on)

    def test_detect_test_command_allowlisted(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        cmd = tools.detect_test_command(root)
        self.assertTrue(cmd is None or cmd[0] in ("python", "npm", "cargo", "go", "dotnet"))


class TestParallelAndNewTools(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = _project()
        self.addCleanup(self.tmp.cleanup)
        (self.root / "package.json").write_text('{"name": "ok"}\n')
        (self.root / "broken.json").write_text('{"name": "broken",}\n')
        (self.root / "todo.py").write_text(
            "def a():\n    pass\n# TODO fix this\n# FIXME later\n")

    def test_search_regex_finds_and_guards(self):
        hits = tools.search_regex(self.root, r"\bboom\s*\(")
        self.assertIn("app.py", {h.path for h in hits})
        # A broken regex yields no hits and never raises.
        self.assertEqual(tools.search_regex(self.root, r"("), [])

    def test_search_literal_unchanged_with_regex_flag_off(self):
        hits = tools.search_text(self.root, "boom")
        self.assertIn("app.py", {h.path for h in hits})
        # raw regex syntax is literal when regex=False
        self.assertEqual(
            {h.path for h in tools.search_text(self.root, r"\bcont\b")}, set())

    def test_search_many_runs_each_pattern(self):
        agg = tools.search_many(self.root, ["boom", "handle"])
        self.assertIn("boom", agg)
        self.assertIn("handle", agg)
        self.assertTrue(any(h.path == "app.py" for h in agg["boom"]))
        # duplicates collapse; short patterns drop
        self.assertEqual(tools.search_many(self.root, ["x", "", "x"]), {})

    def test_find_symbols_multiple(self):
        agg = tools.find_symbols(self.root, ["boom", "Handler"])
        self.assertIn("boom", agg)
        self.assertIn("Handler", agg)
        self.assertTrue(agg["boom"])  # definition ranked first

    def test_recent_files_lists_real_files(self):
        recs = tools.recent_files(self.root, limit=8)
        paths = {r["path"] for r in recs}
        self.assertIn("app.py", paths)
        self.assertNotIn(".venv/evil.py", paths)
        for r in recs:
            self.assertTrue(r["minutes_ago"] >= 0)

    def test_project_tree_is_bounded_and_excludes_deps(self):
        tree = tools.project_tree(self.root)
        self.assertIn("app.py", tree)
        self.assertNotIn("evil.py", tree)  # .venv excluded

    def test_parse_test_failures_pytest_and_python(self):
        out = ("=============================\n"
               "FAILED tests/test_app.py::Klass::test_boom - AssertionError\n"
               '  File "app.py", line 4, in <module>\n'
               "    boom(None)\n"
               "AssertionError: boom\n")
        hits = tools.parse_test_failures(out)
        paths = {h.path for h in hits}
        self.assertIn("tests/test_app.py", paths)
        self.assertIn("app.py", paths)
        self.assertTrue(all(h.line >= 1 for h in hits))

    def test_parse_test_failures_unittest_module(self):
        out = "FAIL: test_boom (tests.test_app.Klass)\nAssertionError: x\n"
        hits = tools.parse_test_failures(out)
        self.assertIn("tests/test_app.py", {h.path for h in hits})

    def test_check_json_config_reports_bad_json(self):
        notes = tools.check_json_config(self.root)
        bad = [n for n in notes if n.startswith("broken.json")]
        self.assertTrue(bad and "invalid JSON" in bad[0])
        self.assertTrue(all(not n.startswith("package.json")
                            for n in notes), "valid JSON must pass silently")

    def test_check_json_config_empty_when_all_fine(self):
        tmp, root = _project()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(tools.check_json_config(root), [])

    def test_scan_annotations(self):
        items = [("todo.py",
                  (self.root / "todo.py").read_text(encoding="utf-8"))]
        notes = tools.scan_annotations(items)
        self.assertEqual(len(notes), 2)
        import re as _re
        found = {_re.search(r"(TODO|FIXME|XXX|HACK)", n["note"],
                            _re.IGNORECASE).group(1)
                 for n in notes}
        self.assertEqual(found, {"TODO", "FIXME"})
        self.assertEqual({note["path"] for note in notes}, {"todo.py"})

    def test_run_map_preserves_order_and_isolates(self):
        out = tools._run_map(lambda i: i ** 2, [1, 2, 3])
        self.assertEqual(out, [1, 4, 9])

        def half_boom(i):
            if i == 2:
                raise RuntimeError("no")
            return i
        out = tools._run_map(half_boom, [1, 2, 3])
        self.assertEqual(out[0], 1)
        self.assertIsNone(out[1])
        self.assertEqual(out[2], 3)


if __name__ == "__main__":
    unittest.main()
