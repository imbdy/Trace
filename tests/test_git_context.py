"""Tests: best-effort git blame/log enrichment (git_context)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from trace_backend.git_context import finding_git_context


class TestGitContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        if shutil.which("git") is None:
            self.skipTest("git is not available on PATH")
        env = dict(os.environ,
                   GIT_AUTHOR_NAME="Alice", GIT_AUTHOR_EMAIL="alice@example.com",
                   GIT_COMMITTER_NAME="Alice", GIT_COMMITTER_EMAIL="alice@example.com")

        def git(*args: str) -> None:
            subprocess.run(["git", "-c", "init.defaultBranch=main", *args],
                           cwd=str(self.root), env=env, check=True,
                           capture_output=True, text=True)

        git("init")
        (self.root / "app.py").write_text("print(1)\n", encoding="utf-8")
        git("add", "app.py")
        git("commit", "-m", "first")
        (self.root / "app.py").write_text("print(2)\n", encoding="utf-8")
        git("add", "app.py")
        git("commit", "-m", "second")
        self.short = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(self.root), env=env,
            capture_output=True, text=True, check=True).stdout.strip()

    def test_real_repo_reports_commit(self):
        ctx = finding_git_context(self.root, "app.py", 1)
        self.assertIn("last changed in commit", ctx)
        self.assertIn(self.short, ctx)
        self.assertIn("Alice", ctx)

    def test_non_repo_returns_empty(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.assertEqual(finding_git_context(Path(other.name), "x.py", 1), "")

    def test_path_escaping_root_returns_empty(self):
        self.assertEqual(finding_git_context(self.root, "../escape.py", 1), "")

    def test_untracked_file_returns_empty(self):
        (self.root / "new.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(finding_git_context(self.root, "new.py", 1), "")

    def test_none_root_and_empty_path_return_empty(self):
        self.assertEqual(finding_git_context(None, "app.py", 1), "")
        self.assertEqual(finding_git_context(self.root, "", 1), "")


if __name__ == "__main__":
    unittest.main()
