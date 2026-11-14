"""观察试点的候选语义、来源抽样和审核版本绑定。"""

import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_motion_import import bvh_fixture

from data.runtime.native_motion import SampledMotion, SourceInfo
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.ingest.import_motion_dataset import run_batch
from data.tools.review.native_review_store import NativeReviewStore, load_native_review_inputs
from data.tools.build.soma_observation import LABELS, intervals, observe, select_clips, sha


class SomaObservationTests(unittest.TestCase):
    def test_parameterized_observation(self):
        for case in ("stable_intervals", "candidate_semantics", "selection", "sidecar_binding"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                if case == "stable_intervals":
                    self.assertEqual(intervals([True, False, True, True, False, True, True, True], 10, .2), [[2, 3], [5, 7]])
                elif case == "candidate_semantics":
                    skeleton, contract = load_skeleton_contract(Path(__file__).parents[1] / "data/skeletons/soma_uniform_v1.json")
                    positions = np.tile(skeleton.rest_positions, (21, 1, 1))
                    positions[:, 0, 0] += np.arange(21) * .1
                    motion = SampledMotion(skeleton, np.arange(21) / 10, positions,
                        np.tile(skeleton.rest_rotations, (21, 1, 1)), np.tile(skeleton.rest_scales, (21, 1, 1)),
                        np.ones((78, 3), dtype=bool), SourceInfo("fixture", {}, "fixture"), 2., 10., 0)
                    row = dict.fromkeys((*LABELS, "take_org_name", "take_name", "take_date", "actor_uid", "is_mirror"), "source label")
                    result = observe(motion, row, "walk", contract["roles"])
                    self.assertEqual(result["contact_truth"], "unknown_no_scene_geometry")
                    self.assertEqual(result["hard_constraints"], [])
                    self.assertFalse(result["stability_candidates"]["left_foot"]["intervals"])
                    np.testing.assert_allclose(result["metrics"]["hips_displacement_m"], [2, 0, 0], atol=1e-10)
                    self.assertTrue(all(e["kind"] in {"source_start", "source_end"} for e in result["phase_candidates"]))
                elif case == "selection":
                    rows = []
                    prefixes = [("walk_ff_start_001", "Basic Locomotion Neutral"), ("walk_ff_loop_001", "Basic Locomotion Neutral"),
                        ("walk_ff_stop_001", "Basic Locomotion Neutral"), ("jog_ff_start_001", "Basic Locomotion Neutral"),
                        ("jog_ff_loop_001", "Basic Locomotion Neutral"), ("jog_ff_stop_001", "Basic Locomotion Neutral"),
                        ("idle_turn_270_001", "Basic Locomotion Neutral"), ("idle_turn_360_001", "Basic Locomotion Neutral"),
                        ("idle_right_R_001", "Basic Locomotion Neutral"), ("idle_hands_on_back_loop_001", "Basic Locomotion Neutral"),
                        ("crouch_idle_001", "Advanced Locomotion"), ("crouch_ff_loop_001", "Advanced Locomotion"),
                        ("turn_crouch_001", "Advanced Locomotion"), ("h_b_w_stand_to_crouch_001", "Advanced Locomotion"),
                        ("h_b_w_crouch_to_stand_001", "Advanced Locomotion")]
                    prefixes += [(f"jump_{i:03}", "Baseline") for i in range(8)]
                    prefixes += [(f"vault_over_R_{i:03}", "Advanced Locomotion") for i in range(2)]
                    prefixes += [(f"come_up_{i}cm_box_001", "Baseline") for i in range(5)]
                    (root / "soma_uniform").mkdir()
                    for i, (prefix, category) in enumerate(prefixes):
                        name = prefix + "__actor"
                        path = "soma_uniform/" + name + ".bvh"
                        (root / path).touch()
                        row = {"filename": name, "take_org_name": prefix, "actor_uid": str(i), "is_mirror": "False",
                               "move_duration_frames": "240", "category": category, "move_soma_uniform_path": path}
                        rows.extend([row, dict(row, is_mirror="True"), dict(row)])
                    selected, coverage = select_clips(rows, root)
                    self.assertEqual(len(selected), 30)
                    self.assertEqual(len({item["family"] for item in selected}), 30)
                    self.assertTrue(all(v["selected"] == v["requested"] for v in coverage.values()))
                else:
                    source = bvh_fixture(root)
                    report = run_batch({"dataset": "fixture", "clips": [{"format": "bvh", "path": str(source),
                        "profile": {"meters_per_unit": 1, "basis": np.eye(3).tolist(), "root_joint": "Root"}}]}, root, "batch", fps=2)
                    record = report["results"][0]
                    reports = root / "reports/fixture/batch"
                    sidecar = reports / "observation.json"
                    sidecar.write_text(json.dumps({"schema": "motionweaver_observation", "version": 1,
                        "source_id": record["source_id"], "frames": 3, "fps": 2}), encoding="utf-8")
                    record.update(observation=str(sidecar), observation_sha256=sha(sidecar))
                    (reports / "run.json").write_text(json.dumps(report), encoding="utf-8")
                    row = {"asset": record["source_id"], "name": "fixture", "frames": 3, "joints": 2, "fps": 2,
                           "duration": 1, "raw": record["raw"], "processed": record["processed"]}
                    index = reports / "export-index.csv"
                    with index.open("w", newline="", encoding="utf-8") as stream:
                        writer = csv.DictWriter(stream, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
                    (reports / "acceptance.json").write_text(json.dumps({"batches": ["batch"], "status_counts": {"normalized": 1},
                        "raw_and_processed_readback_errors": [], "source_endpoint_gaps": []}), encoding="utf-8")
                    index, entries, digest = load_native_review_inputs(index, root)
                    store = NativeReviewStore(root, index, entries, digest)
                    mid = entries[0]["id"]
                    self.assertIn("observation", json.loads(gzip.decompress(store.clip(mid))))
                    sidecar.write_text("{}", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "观察标注内容已改变"):
                        store.clip(mid)


if __name__ == "__main__":
    unittest.main()
