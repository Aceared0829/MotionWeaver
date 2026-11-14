"""评测报告合并的口径门禁。

为什么需要：模型报告与基线报告的字段结构不同（一个按 scenario 平铺，
一个用 "scenario/baseline" 组合键），合并时最容易犯的错是
把 hold 当成 Lerp、或者把缺失值写成 0 让倍数虚高。
这里锁住三件事：四档顺序、倍数方向、缺失值不被伪造成数字。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from merge_evaluation_report import SCENARIOS, merge, to_markdown  # noqa: E402

MODEL = {
    "release": "/data/release",
    "checkpoint": "/data/best.pt",
    "split": "test",
    "scenarios": {
        "no_reference": {"non_anchor_pose_rmse_cm": 5.0, "median_non_anchor_pose_rmse_cm": 4.5,
                         "worst_clip_rmse_cm": 30.0, "foot_slip_ratio_mean": 7.0,
                         "clips": 100},
        "middle_reference": {"non_anchor_pose_rmse_cm": 3.0, "median_non_anchor_pose_rmse_cm": 2.8,
                             "worst_clip_rmse_cm": 20.0, "foot_slip_ratio_mean": 8.0,
                             "clips": 100},
        "end_reference": {"non_anchor_pose_rmse_cm": 3.0, "median_non_anchor_pose_rmse_cm": 2.8,
                          "worst_clip_rmse_cm": 20.0, "foot_slip_ratio_mean": 8.0,
                          "clips": 100},
        "middle_and_end": {"non_anchor_pose_rmse_cm": 2.0, "median_non_anchor_pose_rmse_cm": 1.9,
                           "worst_clip_rmse_cm": 10.0, "foot_slip_ratio_mean": 9.0,
                           "clips": 100},
    },
}

BASELINES = {
    "aggregate": {
        "no_reference/hold": {"non_anchor_pose_rmse_cm": 25.0},
        "no_reference/linear_reference": {"non_anchor_pose_rmse_cm": 16.0},
        "middle_reference/hold": {"non_anchor_pose_rmse_cm": 9.0},
        "middle_reference/linear_reference": {"non_anchor_pose_rmse_cm": 7.0},
        "end_reference/hold": {"non_anchor_pose_rmse_cm": 9.0},
        "end_reference/linear_reference": {"non_anchor_pose_rmse_cm": 7.0},
        "middle_and_end/hold": {"non_anchor_pose_rmse_cm": 4.0},
        "middle_and_end/linear_reference": {"non_anchor_pose_rmse_cm": 3.0},
    },
    "samples": [{"clip_index": 0}],
}


class MergeEvaluationReportTests(unittest.TestCase):
    def test_rows_follow_the_frozen_scenario_order(self):
        rows = merge(MODEL, BASELINES)
        self.assertEqual([row["scenario"] for row in rows], list(SCENARIOS))

    def test_linear_reference_and_hold_are_not_swapped(self):
        rows = {row["scenario"]: row for row in merge(MODEL, BASELINES)}
        # no_reference 档：Lerp 16 / Hold 25，模型 5 → 倍数必须是 3.2 而不是 5
        self.assertAlmostEqual(rows["no_reference"]["linear_reference_rmse_cm"], 16.0)
        self.assertAlmostEqual(rows["no_reference"]["hold_rmse_cm"], 25.0)
        self.assertAlmostEqual(rows["no_reference"]["gain_vs_linear_reference"], 3.2)
        self.assertAlmostEqual(rows["no_reference"]["gain_vs_hold"], 5.0)

    def test_missing_baseline_stays_none_instead_of_zero(self):
        baselines = {"aggregate": {key: value for key, value in BASELINES["aggregate"].items()
                                   if "hold" not in key}}
        rows = merge(MODEL, baselines)
        for row in rows:
            self.assertIsNone(row["hold_rmse_cm"])
            self.assertIsNone(row["gain_vs_hold"])
            self.assertIsNotNone(row["gain_vs_linear_reference"])

    def test_zero_model_error_does_not_produce_infinite_gain(self):
        model = json.loads(json.dumps(MODEL))
        model["scenarios"]["no_reference"]["non_anchor_pose_rmse_cm"] = 0.0
        rows = merge(model, BASELINES)
        self.assertIsNone(rows[0]["gain_vs_linear_reference"])

    def test_markdown_marks_missing_values_with_a_dash(self):
        text = to_markdown(merge(MODEL, BASELINES), MODEL, BASELINES)
        self.assertIn("| 无参考帧 | **5.00** | 16.00 | 25.00 | 3.20× | 7.00× |", text)

    def test_cli_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            model, baselines = folder / "model.json", folder / "baselines.json"
            model.write_text(json.dumps(MODEL), encoding="utf-8")
            baselines.write_text(json.dumps(BASELINES), encoding="utf-8")
            out, md = folder / "comparison.json", folder / "comparison.md"
            subprocess.run([sys.executable, str(REPO / "tools" / "merge_evaluation_report.py"),
                            "--model", str(model), "--baselines", str(baselines),
                            "--output", str(out), "--markdown", str(md)],
                           check=True, capture_output=True)
            report = json.loads(out.read_text(encoding="utf-8"))
            # samples 逐窗口明细不进合并报告：它会让报告涨到几十兆
            self.assertNotIn("samples", report["baselines"])
            self.assertEqual(len(report["comparison"]), 4)
            self.assertIn("refpose 评测对照", md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
