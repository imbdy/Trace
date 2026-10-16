"""Tests: schemas round-trip + report parsing edge cases."""

import unittest

from trace_backend.investigation_engine import InvestigationEngine
from trace_backend.schemas import InvestigationReport, InvestigationStatus


class TestSchemas(unittest.TestCase):
    def test_report_json_round_trip(self):
        r = InvestigationReport(investigation_id="inv-x", summary="s",
                                root_cause="r", confidence="medium")
        d = r.to_dict()
        self.assertEqual(d["investigation_id"], "inv-x")
        import json
        self.assertEqual(json.loads(r.to_json())["confidence"], "medium")

    def test_parse_valid_model_json(self):
        raw = ('{"summary": "s", "root_cause": "rc", "confidence": "high", '
               '"confidence_rationale": "why", "findings": [{"title": "t", '
               '"file_path": "a.py", "start_line": 1, "end_line": 2}], '
               '"recommended_fix": {"description": "d"}, '
               '"alternative_hypotheses": [], "needs_more_info": [], '
               '"evidence_summary": "e"}')
        rep = InvestigationEngine._parse_report("inv-1", raw)
        self.assertEqual(rep.confidence, "high")
        self.assertEqual(len(rep.findings), 1)

    def test_parse_fenced_json(self):
        raw = '```json\n{"summary": "s", "root_cause": "r", "confidence": "low"}\n```'
        rep = InvestigationEngine._parse_report("inv-1", raw)
        self.assertEqual(rep.confidence, "low")

    def test_malformed_output_degrades_honestly(self):
        rep = InvestigationEngine._parse_report("inv-1", "not json at all!!!")
        self.assertEqual(rep.confidence, "low")
        self.assertTrue(any("Malformed" in w for w in rep.warnings))
        # Raw output preserved, not silently dropped.
        self.assertIn("not json", rep.alternative_hypotheses[0])

    def test_investigation_status_round_trip(self):
        s = InvestigationStatus(
            investigation_id="inv-1", status="done", progress=1.0,
            stage="report", investigation_type="explain")
        d = s.to_dict()
        self.assertEqual(d["investigation_type"], "explain")
        back = InvestigationStatus(**d)
        self.assertEqual(back.investigation_type, "explain")
        # default stays debug for backward compatibility
        plain = InvestigationStatus(investigation_id="i", status="done")
        self.assertEqual(plain.investigation_type, "debug")

    def test_invalid_confidence_downgraded(self):
        from pathlib import Path
        import tempfile
        from trace_backend.validator import sanitize_report
        with tempfile.TemporaryDirectory() as td:
            rep = InvestigationReport(investigation_id="i", summary="s",
                                      root_cause="r", confidence="certainly")
            out = sanitize_report(Path(td), rep)
            self.assertEqual(out.confidence, "low")


if __name__ == "__main__":
    unittest.main()
