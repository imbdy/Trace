"""Tests: file/line verification against real files on disk."""

import tempfile
import unittest
from pathlib import Path

from trace_backend.schemas import (
    Finding,
    InvestigationReport,
    RecommendedFix,
)
from trace_backend.validator import (
    sanitize_report,
    verify_fix_diff,
    verify_location,
)


class TestVerifyLocation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "app.py").write_text(
            "import os\n\n\ndef boom():\n    raise ValueError('x')\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_location_returns_excerpt(self):
        v = verify_location(self.root, "app.py", 4, 5)
        self.assertTrue(v["exists"] and v["line_valid"])
        self.assertIn("def boom", v["excerpt"])

    def test_missing_file(self):
        v = verify_location(self.root, "nope.py", 1, 2)
        self.assertFalse(v["exists"])
        self.assertIn("not found", v["warning"])

    def test_out_of_range_lines_rejected(self):
        v = verify_location(self.root, "app.py", 99, 120)
        self.assertTrue(v["exists"])
        self.assertFalse(v["line_valid"])

    def test_path_traversal_rejected(self):
        v = verify_location(self.root, "../outside.py", 1, 1)
        self.assertFalse(v["exists"] and v["line_valid"])

    def test_sanitize_marks_invented_paths(self):
        report = InvestigationReport(
            investigation_id="inv-1", summary="s", root_cause="r", confidence="high",
            findings=[Finding(title="real", file_path="app.py", start_line=4, end_line=4),
                      Finding(title="fake", file_path="ghost.py", start_line=10, end_line=12)])
        out = sanitize_report(self.root, report)
        self.assertTrue(out.findings[0].verified)
        self.assertFalse(out.findings[1].verified)
        self.assertTrue(any("ghost.py" in w for w in out.warnings))
        self.assertIn("UNVERIFIED", out.findings[1].evidence)


class TestDiffVerification(unittest.TestCase):
    """Feature 3: the recommended fix's diff is re-grounded against disk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Line 3 = "def boom(x):", line 4 = "    return x.strip()".
        (self.root / "app.py").write_text(
            "import os\n\ndef boom(x):\n    return x.strip()\n\n\nboom(None)\n",
            encoding="utf-8")

    def _report(self, diff: str | None) -> InvestigationReport:
        return InvestigationReport(
            investigation_id="inv-1", summary="s", root_cause="r", confidence="high",
            recommended_fix=RecommendedFix(description="guard None", diff=diff))

    def test_matching_diff_is_verified(self):
        # Old side (context + removed) matches app.py lines 3-4.
        diff = (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -3,2 +3,3 @@\n"
            " def boom(x):\n"
            "-    return x.strip()\n"
            "+    if x is None:\n"
            "+        return None\n"
            "+    return x.strip()\n"
        )
        out = sanitize_report(self.root, self._report(diff))
        self.assertTrue(out.recommended_fix.diff_verified)
        self.assertFalse(any("could not be verified" in w for w in out.warnings))

    def test_fabricated_path_is_unverified_with_warning(self):
        diff = (
            "--- a/ghost.py\n"
            "+++ b/ghost.py\n"
            "@@ -3,1 +3,2 @@\n"
            " anything\n"
            "+    more\n"
        )
        out = sanitize_report(self.root, self._report(diff))
        self.assertFalse(out.recommended_fix.diff_verified)
        self.assertTrue(any("could not be verified" in w and "ghost.py" in w
                            for w in out.warnings))

    def test_wrong_context_is_unverified(self):
        # Path exists, but the old-side context does not match the real file.
        diff = (
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -3,1 +3,2 @@\n"
            " this line is not in the file\n"
            "+    added\n"
        )
        out = sanitize_report(self.root, self._report(diff))
        self.assertFalse(out.recommended_fix.diff_verified)
        self.assertTrue(any("could not be verified" in w for w in out.warnings))

    def test_none_and_empty_diff_are_skipped(self):
        for d in (None, "", "   "):
            out = sanitize_report(self.root, self._report(d))
            self.assertFalse(out.recommended_fix.diff_verified)
            self.assertFalse(any("could not be verified" in w for w in out.warnings))

    def test_verify_fix_diff_never_raises_on_garbage(self):
        fix = RecommendedFix(description="d", diff="not a diff at all\n@@ broken")
        ok, warns = verify_fix_diff(self.root, fix)  # noqa: F841
        self.assertIn(ok, (True, False))


if __name__ == "__main__":
    unittest.main()
