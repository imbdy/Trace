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


if __name__ == "__main__":
    unittest.main()
