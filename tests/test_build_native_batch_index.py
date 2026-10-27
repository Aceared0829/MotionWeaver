"""批次索引重建：从落盘产物生成索引与验收汇总，并与审核入口对齐。"""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_motion_import import bvh_fixture

from data.tools.build_native_batch_index import INDEX_FIELDS, build
from data.tools.import_motion_dataset import run_batch
from data.tools.native_review_store import load_native_review_inputs


def fixture_batch(folder, fps=2):
    data_root = folder / "storage"
    path = bvh_fixture(folder)
    profile = {"meters_per_unit": 1, "basis": np.eye(3).tolist(), "root_joint": "Root"}
    run_batch({"dataset": "fixture", "clips": [{"format": "bvh", "path": str(path), "profile": profile}]},
              data_root, "batch", fps=fps)
    return data_root, path, profile


class BuildNativeBatchIndexTests(unittest.TestCase):
    def test_parameterized_rebuild(self):
        for case in ("rebuild", "review_roundtrip", "tampered_output", "idempotent"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                data_root, _, _ = fixture_batch(folder)
                report = data_root / "reports/fixture/batch"
                # 导入层按设计不产出索引；重建前审核入口必然失败。
                self.assertFalse((report / "export-index.csv").exists())
                with self.assertRaises(FileNotFoundError):
                    load_native_review_inputs(report / "export-index.csv", data_root)

                if case == "tampered_output":
                    # 内容被改写后不得进入索引，也不得留下看起来合格的空索引。
                    binary = next((data_root / "processed/fixture/batch/clips").rglob("arrays.bin"))
                    payload = bytearray(binary.read_bytes())
                    payload[0] ^= 1
                    binary.write_bytes(payload)
                    with self.assertRaises(ValueError):
                        build(data_root, "batch", "fixture")
                    self.assertFalse((report / "export-index.csv").exists())
                    continue

                result = build(data_root, "batch", "fixture")
                self.assertEqual(result["rows"], 1)
                with (report / "export-index.csv").open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(list(rows[0]), INDEX_FIELDS)
                self.assertEqual(rows[0]["status"], "normalized")
                self.assertEqual(rows[0]["joints"], "2")

                if case == "idempotent":
                    first = (report / "export-index.csv").read_bytes()
                    build(data_root, "batch", "fixture")
                    self.assertEqual((report / "export-index.csv").read_bytes(), first)
                    continue

                # 重建结果必须能被真正的审核入口消费。
                index, entries, digest = load_native_review_inputs(report / "export-index.csv", data_root)
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["frames"], 3)
                self.assertTrue(digest)

    def test_unresolved_batch_is_not_indexed(self):
        """未产出结果的条目必须留在验收汇总的排除名单里，而不是伪装成成功。"""
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            data_root, _, _ = fixture_batch(folder)
            report = data_root / "reports/fixture/batch"
            batch = json.loads((report / "run.json").read_text(encoding="utf-8"))
            failed = dict(batch["results"][0], status="needs_skeleton_review", error="骨架缺少四肢")
            batch["results"].append(failed)
            (report / "run.json").write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
            result = build(data_root, "batch", "fixture")
            self.assertEqual(result["rows"], 1)
            self.assertEqual(result["excluded"], 1)
            acceptance = json.loads((report / "acceptance.json").read_text(encoding="utf-8"))
            self.assertEqual(acceptance["status_counts"]["normalized"], 1)
            self.assertEqual(len(acceptance["excluded"]), 1)

    def test_later_batch_replaces_earlier_asset(self):
        """补跑批次替换同名资产：合并后条数只增不重复，归属记录在 clip_batches。

        真实批次 `20261015-full-export` 的 1844 + 36 + 91 = 1971 正是因为 1 条被替换才成为 1970。
        """
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            data_root, path, profile = fixture_batch(folder, fps=2)
            # 第一批次产出一条；再做一个同源补跑批次。
            retry = data_root / "reports/fixture/batch-retry"
            retry.mkdir(parents=True)
            source = json.loads((data_root / "reports/fixture/batch/run.json").read_text(encoding="utf-8"))
            (retry / "run.json").write_text(
                json.dumps({**source, "run_id": "batch-retry"}, ensure_ascii=False), encoding="utf-8")
            result = build(data_root, ["batch", "batch-retry"], "fixture")
            self.assertEqual(result["rows"], 1)
            acceptance = json.loads((retry.parent / "batch" / "acceptance.json").read_text(encoding="utf-8"))
            self.assertEqual(acceptance["batches"], ["batch", "batch-retry"])
            self.assertEqual(len(acceptance["clip_batches"]), 1)
            # 后一批次覆盖归属。
            self.assertEqual(list(acceptance["clip_batches"].values()), ["batch-retry"])


if __name__ == "__main__":
    unittest.main()
