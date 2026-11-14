"""条件动作数据契约的真实帧、可见参考与离线基线回归。"""

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from test_unreal_dataset import pose_only_fixture

from data.runtime.conditioned_motion import build_index, load_window, with_references
from data.tools.audit.audit_conditioned_dataset import audit
from data.tools.review.conditioned_review_store import (
    ConditionedReviewStore,
    load_review_inputs,
)
from data.tools.publish.freeze_conditioned_dataset import freeze
from data.tools.ingest.prepare_unreal_dataset import asset_family, prepare
from training.evaluation.evaluate_conditioned_baselines import evaluate, predict


def named_asset(bucket, prefix):
    for number in range(26 * 26):
        suffix = chr(65 + number // 26) + chr(65 + number % 26)
        asset = f"/Game/Animations/Walk/{prefix}{suffix}.{prefix}{suffix}"
        group = asset_family(asset)
        if int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 10 == bucket:
            return asset
    raise AssertionError("未找到测试分区资产")


def prepared_fixture(folder, with_short=True, distinct_train=False):
    source = folder / "source"
    source.mkdir()
    definitions = [("train.json", named_asset(5, "Train"), 100),
                   ("test.json", named_asset(0, "Test"), 100)]
    if with_short:
        definitions.append(("short.json", named_asset(6, "Short"), 30))
    for filename, asset, length in definitions:
        clip = pose_only_fixture()
        clip["asset"] = asset
        clip["frames"] = clip["frames"][:length]
        clip["root_frames"] = clip["root_frames"][:length]
        if distinct_train and filename == "train.json":
            for index, root in enumerate(clip["root_frames"]):
                root[0] += index * 0.1
        (source / filename).write_text(json.dumps(clip), encoding="utf-8")
    (source / "manifest.json").write_text(json.dumps({"schema_version": 2,
        "training_contract": "pose_only_root_authoritative",
        "clips": [filename for filename, _, _ in definitions]}), encoding="utf-8")
    prepared = folder / "prepared"
    prepare(source, prepared, split=True, short_clip_policy="hold")
    return prepared


class ConditionedMotionContractTests(unittest.TestCase):
    def test_existing_review_tool_reads_prepared_audit_and_persists_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            prepared = prepared_fixture(folder)
            index = folder / "conditional"
            build_index(prepared, index)
            audit_folder = folder / "audit"
            audit(index, audit_folder)
            contract, report, entries, queue_hash = load_review_inputs(index, audit_folder)
            self.assertTrue(entries)
            library = folder / "library"
            store = ConditionedReviewStore(library, contract, report, entries, queue_hash)
            clip = json.loads(gzip.decompress(store.clip(entries[0]["id"])))
            self.assertEqual(clip["fps"], 30)
            self.assertEqual(clip["frames"], entries[0]["frames"])
            self.assertEqual(len(clip["source"]["positions"]), clip["frames"] * contract["bone_count"] * 3)
            raw_file = prepared / contract["clips"][int(entries[0]["id"])]["raw_file"]
            with np.load(raw_file, allow_pickle=False) as raw:
                root = raw["root_track"][:, :3]
            expected_root = np.stack((root[:, 2], -root[:, 0], root[:, 1]), axis=-1) * 100
            np.testing.assert_allclose(np.asarray(clip["source"]["root_positions"]).reshape(-1, 3),
                                       expected_root, atol=0.001)
            self.assertIsNone(clip["target"])
            store.save({"id": entries[0]["id"], "status": "unsure", "note": "复核中"})
            reopened = ConditionedReviewStore(library, contract, report, entries, queue_hash)
            self.assertEqual(reopened.decisions()[entries[0]["id"]]["note"], "复核中")

    def test_real_frames_references_and_offline_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            prepared = prepared_fixture(folder)
            index = folder / "conditional"
            contract = build_index(prepared, index, history_frames=24, future_frames=24, stride_frames=8)
            self.assertEqual(contract["window_count"], 16)
            self.assertEqual(contract["clips_without_generation_window"], 1)
            self.assertEqual(contract["stats"]["pose_m"]["valid_frames"], 130)
            self.assertEqual(contract["window_split_counts"], {"train": 8, "test": 8})
            self.assertFalse(contract["input_contract"]["scene_context_available"])
            self.assertEqual(contract["array_coordinate_system"], "motion_y_up_meters_root_relative")
            self.assertEqual(contract["array_axes_from_ue"], {"x": "-ue_y", "y": "ue_z", "z": "ue_x"})
            rows = [json.loads(line) for line in (index / "windows.jsonl").read_text().splitlines()]
            row = next(row for row in rows if contract["clips"][row["clip_index"]]["split"] == "test")
            sample = load_window(index, row, contract=contract)
            np.testing.assert_allclose(sample["input"]["history_root_local"][-1, :3], 0, atol=1e-6)
            self.assertIn("future_root_plan_local", sample["input"])
            self.assertNotIn("mean_desired_velocity", sample["input"])
            velocity_sample = load_window(index, row, control_mode="mean_desired_velocity", contract=contract)
            self.assertIn("mean_desired_velocity", velocity_sample["input"])
            self.assertNotIn("future_root_plan_local", velocity_sample["input"])
            self.assertNotIn("future_pose", sample["input"])
            self.assertFalse(sample["input"]["reference_frame_mask"].any())
            anchored = with_references(sample, (12, 23))
            visible = anchored["input"]
            self.assertEqual(visible["reference_frame_mask"].tolist().count(True), 2)
            self.assertTrue(np.all(visible["reference_pose"][~visible["reference_frame_mask"]] == 0))
            for method in ("hold", "linear_reference"):
                pose, rotation = predict(visible, method)
                np.testing.assert_allclose(pose[[12, 23]], sample["target"]["future_pose"][[12, 23]], atol=1e-6)
                np.testing.assert_allclose(rotation[[12, 23]], sample["target"]["future_rotations"][[12, 23]], atol=1e-6)
            report = evaluate(index, folder / "baseline", split="test")
            self.assertTrue(report["oracle_root_condition"])
            self.assertEqual(report["aggregate"]["no_reference/hold"]["clips"], 1)
            self.assertEqual(report["aggregate"]["middle_and_end/linear_reference"]["anchor_pose_rmse_cm"], 0)
            audit_report = audit(index, folder / "audit")
            self.assertEqual(audit_report["checked_clips"], 3)
            self.assertFalse(audit_report["integrity_errors"])
            self.assertFalse(audit_report["cross_split_contract_groups"])
            self.assertEqual(audit_report["flag_counts"].get("contact_index_mismatch", 0), 0)
            self.assertEqual(audit_report["flag_counts"].get("exact_motion_cross_split"), 2)
            filtered = folder / "filtered"
            filtered_contract = build_index(prepared, filtered, history_frames=24, future_frames=24,
                                            stride_frames=8, exclude_train_indices=(0,))
            self.assertEqual(filtered_contract["excluded_train_clip_indices"], [0])
            self.assertEqual(filtered_contract["window_count"], 8)
            self.assertEqual(filtered_contract["stats"]["pose_m"]["valid_frames"], 30)
            filtered_audit = audit(filtered, folder / "filtered_audit")
            self.assertEqual(filtered_audit["active_exact_motion_cross_split_groups"], 0)
            self.assertFalse(filtered_audit["training_exclusion_candidates"])

    def test_missing_root_rejected_before_complete_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            prepared = prepared_fixture(folder, with_short=False)
            raw_path = prepared / "raw_00000.npz"
            with np.load(raw_path) as raw:
                fields = {key: raw[key] for key in raw.files if key != "root_track"}
            np.savez_compressed(raw_path, **fields)
            output = folder / "invalid_index"
            with self.assertRaisesRegex(ValueError, "独立 Root"):
                build_index(prepared, output)
            self.assertFalse((output / "contract.json").exists())

    def test_freeze_requires_complete_approved_cohort_and_fixed_skeleton(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            prepared = prepared_fixture(folder, with_short=False, distinct_train=True)
            index = folder / "conditional"
            build_index(prepared, index)
            audit_folder = folder / "audit"
            audit(index, audit_folder)
            baseline_folder = folder / "baseline"
            evaluate(index, baseline_folder, split="test")
            contract, report, entries, queue_hash = load_review_inputs(index, audit_folder)
            store = ConditionedReviewStore(folder / "library", contract, report, entries, queue_hash)
            for entry in entries:
                store.save({"id": entry["id"], "status": "approved", "note": "checked"})
            summary = freeze(index, audit_folder, baseline_folder / "report.json", folder / "library",
                             folder / "release", folder / "release-lock.json")
            self.assertEqual(summary["review"]["approved"], len(entries))
            self.assertEqual(summary["artifacts_sha256"]["review_decisions.json"],
                             hashlib.sha256((folder / "release" / "review_decisions.json").read_bytes()).hexdigest())
            store.save({"id": entries[0]["id"], "status": "rejected", "note": "bad"})
            with self.assertRaisesRegex(ValueError, "未通过结论"):
                freeze(index, audit_folder, baseline_folder / "report.json", folder / "library",
                       folder / "release2", folder / "release-lock2.json")
            (prepared / "skeleton.json").write_text((prepared / "skeleton.json").read_text() + " ")
            with self.assertRaisesRegex(ValueError, "来源骨架已改变"):
                store.clip(entries[0]["id"])


if __name__ == "__main__":
    unittest.main()
