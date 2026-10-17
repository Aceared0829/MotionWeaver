"""优化保持实际回读哈希、共享 FK 输出与显式代码升级边界。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from data.runtime.motion_kinematics import world_positions
from data.runtime.motion_processing import process_motion
from data.runtime.native_motion import SampledMotion, SourceInfo
from data.runtime.native_motion_io import read_sampled_motion, write_sampled_motion
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.process_soma_dataset import hashes, verify_resume_manifest
from data.tools.soma_observation import sha, write_json


class SomaProcessingOptimizationTests(unittest.TestCase):
    def test_parameterized_optimization_contract(self):
        for case in ("readback_hashes", "shared_fk", "code_upgrade", "changed_policy"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                if case in ("code_upgrade", "changed_policy"):
                    previous = {"policy": {"version": "fixed"}, "code_sha256": {"processor": "old"}}
                    write_json(folder / "manifest.json", previous)
                    old_sha = sha(folder / "manifest.json")
                    current = copy.deepcopy(previous)
                    current["code_sha256"]["processor"] = "new"
                    receipt = folder / "equivalence.json"
                    write_json(receipt, {"previous_manifest_sha256": old_sha, "code_sha256": current["code_sha256"],
                                         "equivalent_outputs": True})
                    if case == "changed_policy":
                        current["policy"]["version"] = "changed"
                        with self.assertRaisesRegex(ValueError, "处理规则已改变"):
                            verify_resume_manifest(folder, current, receipt)
                        self.assertEqual(sha(folder / "manifest.json"), old_sha)
                    else:
                        with self.assertRaisesRegex(ValueError, "代码"):
                            verify_resume_manifest(folder, current)
                        verify_resume_manifest(folder, current, receipt)
                        self.assertEqual(json.loads((folder / "manifest.json").read_text(encoding="utf-8")), current)
                        self.assertEqual(sha(folder / "manifest-history" / (old_sha + ".json")), old_sha)
                    continue
                skeleton, contract = load_skeleton_contract(Path(__file__).parents[1] / "data/skeletons/soma_uniform_v1.json")
                times = np.arange(21) / 120
                motion = SampledMotion(skeleton, times, np.tile(skeleton.rest_positions, (21, 1, 1)),
                    np.tile(skeleton.rest_rotations, (21, 1, 1)), np.tile(skeleton.rest_scales, (21, 1, 1)),
                    np.ones((78, 3), bool), SourceInfo("fixture", {}, "analytic"), float(times[-1]), 120., 0)
                motion.positions[:, 0, 2] += times
                if case == "readback_hashes":
                    target = folder / "clip"
                    write_sampled_motion(target, motion)
                    receipt = {}
                    restored = read_sampled_motion(target, file_hashes=receipt)
                    self.assertEqual(receipt, hashes(target))
                    np.testing.assert_array_equal(restored.positions, motion.positions)
                else:
                    original, summary = process_motion(motion, contract["roles"])
                    shared, shared_summary = process_motion(motion, contract["roles"], world=world_positions(motion))
                    self.assertEqual(summary, shared_summary)
                    for key in original:
                        np.testing.assert_array_equal(shared[key], original[key])


if __name__ == "__main__":
    unittest.main()
