"""Tests: file/line verification against real files on disk."""

import tempfile
import unittest
from pathlib import Path

from trace_backend.schemas import Finding, InvestigationReport
from trace_backend.validator import sanitize_report, verify_finding, verify_location


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


if __name__ == "__main__":
    unittest.main()
