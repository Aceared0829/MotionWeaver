"""全库标准化的固定偏移、Root 合成、源骨架差异与动态骨长拒绝。"""

import copy
import unittest
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.motion_kinematics import world_positions
from data.runtime.native_motion import SampledMotion, SourceInfo
from data.runtime.native_motion_io import skeleton_signature
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.process_soma_dataset import fixed_soma_motion


class SomaFullProcessingTests(unittest.TestCase):
    def test_parameterized_fixed_contract(self):
        for case in ("identity", "root_composition", "reference_variant", "animated_translation"):
            with self.subTest(case=case):
                fixed, _ = load_skeleton_contract(Path(__file__).parents[1] / "data/skeletons/soma_uniform_v1.json")
                motion = SampledMotion(copy.deepcopy(fixed), np.arange(3) / 120,
                    np.tile(fixed.rest_positions, (3, 1, 1)), np.tile(fixed.rest_rotations, (3, 1, 1)),
                    np.ones((3, 78, 3)), np.ones((78, 3), bool), SourceInfo("fixture", {}, "analytic"), 2 / 120, 120, 0)
                if case == "root_composition":
                    motion.positions[:, 0] = [[0, 0, 0], [1, .5, 2], [2, 1, 4]]
                    motion.rotations[:, 0] = Rotation.from_euler("y", [[0], [.5], [1]]).as_quat()
                elif case == "reference_variant":
                    motion.skeleton.rest_positions[3, 1] += .00045
                    motion.positions[:, 3, 1] += .00045
                elif case == "animated_translation":
                    motion.positions[1, 3, 1] += .1
                    with self.assertRaisesRegex(ValueError, "动画平移"):
                        fixed_soma_motion(motion, fixed)
                    continue
                original = world_positions(motion)
                result, adjustment = fixed_soma_motion(motion, fixed)
                self.assertEqual(skeleton_signature(result.skeleton), skeleton_signature(fixed))
                np.testing.assert_allclose(result.positions[:, 0], 0)
                np.testing.assert_allclose(world_positions(result)[:, 1], original[:, 1], atol=1e-12)
                if case == "reference_variant":
                    self.assertGreater(adjustment, .0004)
                    np.testing.assert_array_equal(result.positions[:, 3], np.tile(fixed.rest_positions[3], (3, 1)))
                else:
                    np.testing.assert_allclose(world_positions(result)[:, 1:], original[:, 1:], atol=1e-12)


if __name__ == "__main__":
    unittest.main()
