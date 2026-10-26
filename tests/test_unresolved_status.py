"""重定向未解决项的状态诚实性：失败条目不得计入 normalized。

原缺陷把待复核条目写成 status="normalized"，而消费者只按 status 过滤，
导致未解决项进入看似合格的审核名单。
"""

import json
import tempfile
import unittest
from pathlib import Path

from data.tools.native_review_store import load_native_review_inputs


def write_batch(root, run_id, records, acceptance_extra=None, index_rows=None):
    report = root / "reports" / "gameanimation_soma" / run_id
    report.mkdir(parents=True, exist_ok=True)
    (report / "run.json").write_text(
        json.dumps({"scope": "fixture", "results": records}, ensure_ascii=False), encoding="utf-8")
    fields = ["asset", "name", "status", "joints", "frames", "duration", "fps",
              "skeleton", "skeleton_signature", "raw", "processed", "error"]
    rows = index_rows if index_rows is not None else []
    with (report / "export-index.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        import csv
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    acceptance = {"batches": [run_id], "status_counts": {"normalized": 0},
                  "raw_and_processed_readback_errors": [], "source_endpoint_gaps": []}
    acceptance.update(acceptance_extra or {})
    (report / "acceptance.json").write_text(json.dumps(acceptance, ensure_ascii=False), encoding="utf-8")
    return report


class UnresolvedStatusTests(unittest.TestCase):
    def test_unresolved_never_counts_as_normalized(self):
        """待复核条目如实记为 needs_skeleton_review，且不进入 normalized 名单。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = {"status": "needs_skeleton_review", "source_id": "face_neck",
                      "raw": str(root / "raw/x"), "processed": str(root / "processed/x"),
                      "raw_sha256": {}, "processed_sha256": {}, "retarget_status": "unresolved",
                      "review_flags": ["needs_skeleton_review"], "review_reason": "重试后仍需核对：缺少四肢"}
            report = write_batch(root, "issues", [record], {
                "included_statuses": ["needs_skeleton_review"],
                "status_counts": {"normalized": 0, "needs_skeleton_review": 1},
            }, index_rows=[{"asset": "face_neck", "name": "face_neck", "status": "needs_skeleton_review",
                            "joints": 875, "frames": 47, "duration": 1.0, "fps": 30,
                            "skeleton": "", "skeleton_signature": "", "raw": str(root / "raw/x"),
                            "processed": str(root / "processed/x"), "error": ""}])
            # 待复核队列自身可被加载（用于展示失败条目的来源骨架）。
            _, entries, _ = load_native_review_inputs(report / "export-index.csv", root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["group"], "issue")
            self.assertIn("needs_skeleton_review", entries[0]["flags"])

    def test_default_acceptance_excludes_non_normalized(self):
        """未声明 included_statuses 时保持历史默认：失败项不得混入。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [
                {"status": "normalized", "source_id": "ok", "raw": str(root / "raw/ok"),
                 "processed": str(root / "processed/ok"), "raw_sha256": {}, "processed_sha256": {}},
                {"status": "needs_skeleton_review", "source_id": "bad", "error": "缺少四肢"},
            ]
            report = write_batch(root, "mixed", records, index_rows=[])
            # 索引为空：消费端必须报错，而不是把 ok 悄悄当成唯一成功项。
            with self.assertRaises(ValueError):
                load_native_review_inputs(report / "export-index.csv", root)

    def test_declared_counts_must_agree(self):
        """声明不纳入 normalized 却仍记 normalized 计数时，必须拒绝而不是给出错名单。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [{"status": "needs_skeleton_review", "source_id": "bad", "error": "缺少四肢"}]
            report = write_batch(root, "inconsistent", records, {
                "included_statuses": ["needs_skeleton_review"],
                "status_counts": {"normalized": 1, "needs_skeleton_review": 1},
            })
            with self.assertRaises(ValueError):
                load_native_review_inputs(report / "export-index.csv", root)


if __name__ == "__main__":
    unittest.main()
