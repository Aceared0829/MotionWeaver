"""独立运动审核：缩放 FK、查看器数据、人工记录及内容变更校验。"""

import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_motion_import import bvh_fixture

from data.runtime.importers.bvh import read_bvh_motion
from data.runtime.native_motion_io import read_sampled_motion
from data.runtime.normalize_motion import normalize_motion
from data.tools.ingest.import_motion_dataset import run_batch
from data.tools.review.native_review_store import (
    NativeReviewStore,
    load_native_review_inputs,
    world_positions,
)


class NativeReviewTests(unittest.TestCase):
    def test_parameterized_review(self):
        for case in ("scaled_fk", "viewer_payload", "persist_decision", "changed_content"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                path = bvh_fixture(folder)
                profile = {"meters_per_unit": 1, "basis": np.eye(3).tolist(), "root_joint": "Root"}
                if case == "scaled_fk":
                    motion = normalize_motion(read_bvh_motion(path, profile), 2)
                    motion.scales[:, 0] = [2, 3, 1]
                    np.testing.assert_allclose(world_positions(motion)[0, 1], [10, 26, 30], atol=1e-8)
                    continue
                data_root = folder / "storage"
                report = run_batch({"dataset": "fixture", "clips": [{"format": "bvh", "path": str(path), "profile": profile}]},
                                   data_root, "batch", fps=2)
                record = report["results"][0]
                report_dir = data_root / "reports/fixture/batch"
                index = report_dir / "export-index.csv"
                row = {"asset": record["source_id"], "name": "walk", "frames": 3, "joints": 2,
                       "fps": 2, "duration": 1, "raw": record["raw"], "processed": record["processed"]}
                with index.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader()
                    writer.writerow(row)
                (report_dir / "acceptance.json").write_text(json.dumps({"batches": ["batch"],
                    "status_counts": {"normalized": 1}, "raw_and_processed_readback_errors": [], "source_endpoint_gaps": []}), encoding="utf-8")
                index, entries, digest = load_native_review_inputs(index, data_root)
                store = NativeReviewStore(data_root, index, entries, digest)
                mid = entries[0]["id"]
                if case == "viewer_payload":
                    payload = json.loads(gzip.decompress(store.clip(mid)))
                    motion = read_sampled_motion(record["processed"])
                    self.assertEqual(payload["frames"], 3)
                    self.assertIsNone(payload["target"])
                    expected = world_positions(motion)[..., [2, 0, 1]] * [100, -100, 100]
                    np.testing.assert_allclose(np.asarray(payload["source"]["positions"]).reshape(3, 2, 3), expected, atol=1e-3)
                elif case == "persist_decision":
                    store.save({"id": mid, "status": "unsure", "note": "第 2 帧需复核"})
                    restored = NativeReviewStore(data_root, index, entries, digest)
                    self.assertEqual(restored.decisions()[mid]["note"], "第 2 帧需复核")
                    with self.assertRaises(ValueError):
                        restored.save({"id": "not-in-cohort", "status": "approved"})
                    self.assertTrue((Path(record["raw"]) / "motion.json").is_file())
                else:
                    binary = Path(record["processed"]) / "arrays.bin"
                    payload = bytearray(binary.read_bytes())
                    payload[0] ^= 1
                    binary.write_bytes(payload)
                    with self.assertRaisesRegex(ValueError, "内容已改变"):
                        store.clip(mid)


if __name__ == "__main__":
    unittest.main()
