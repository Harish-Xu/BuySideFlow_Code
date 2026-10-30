from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from spider_agent_lite.adapter.reporting import export_run_reports


class ReportingTests(unittest.TestCase):
    def write_result(self, root, qid, *, submission=None, snapshot="snapshot-v1", actions=()):
        target = root / qid
        target.mkdir()
        raw = {
            "finished": submission is not None,
            "submission": submission,
            "trajectory": [{"action": action} for action in actions],
            "adapter": {
                "model": "deepseek/deepseek-v4-flash", "run_name": "smoke",
                "db_snapshot_id": snapshot, "actual_run_code_calls": 0,
            },
        }
        path = target / "result.json"
        path.write_text(json.dumps(raw))
        return path

    def test_partial_run_counts_attempts_and_leaves_scores_blank(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = self.write_result(root, "bond_001", actions=(
                "None", "search_tables bond", "run_code\n{}\nEND_RUN_CODE",
                "submit\n{}\nEND_SUBMIT",
            ))
            original = path.read_bytes()
            report = export_run_reports(root)
            self.assertEqual(path.read_bytes(), original)
            self.assertIn("Recorded tasks: 1", report.read_text())
            self.assertIn("Accepted submissions: 0", report.read_text())
            self.assertIn("NOT EVALUATED", report.read_text())
            with next(root.glob("tool_use_*.csv")).open(newline="") as file:
                row = next(csv.DictReader(file))
            self.assertEqual(row["total_turns"], "4")
            self.assertEqual(row["schema_explore_cnt"], "1")
            self.assertEqual(row["run_code_cnt"], "1")
            self.assertEqual(row["submit_cnt"], "1")
            self.assertEqual(row["score"], "")
            self.assertEqual(row["passed"], "")
            preds = json.loads((root / "preds.json").read_text())
            self.assertEqual(preds["bond_001"]["model_patch"], "")
            exported = json.loads((root / "bond_001/bond_001.traj").read_text())
            self.assertEqual(exported["trajectory"][0]["action"], "None")
            evaluated_report = root / report.name.replace("_generation.md", ".md")
            evaluated_report.write_text("existing evaluation")
            export_run_reports(root)
            self.assertEqual(evaluated_report.read_text(), "existing evaluation")

    def test_rejects_mixed_snapshots_before_writing_exports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_result(root, "bond_001")
            self.write_result(root, "bond_002", snapshot="snapshot-v2")
            with self.assertRaisesRegex(ValueError, "different models, runs, or database snapshots"):
                export_run_reports(root)
            self.assertFalse((root / "preds.json").exists())


if __name__ == "__main__":
    unittest.main()
