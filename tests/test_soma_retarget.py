"""重定向的参考轴、独立 Root 运动、BVH 保真与固定骨长四组验收。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.motion_kinematics import world_positions
from data.runtime.native_motion import NativeSkeleton, SampledMotion, SourceInfo
from data.runtime.native_motion_io import skeleton_signature
from data.runtime.retarget_motion import SomaRetargeter, rest_motion
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.retarget_uefn_to_soma import write_bvh

ROOT = Path(__file__).parents[1]


def fixture():
    target, _ = load_skeleton_contract(ROOT / "data/skeletons/soma_uniform_v1.json")
    mapping = json.loads((ROOT / "data/skeletons/mappings/ue_humanoid_to_soma_v1.json").read_text())
    inverse = {t: s for t, s in mapping["joints"].items()}
    # 每个骨骼使用不同局部坐标轴，整体再朝向 -X，并改变体型比例。
    count = len(target.names)
    bases = Rotation.from_euler("xyz", np.arange(count * 3).reshape(count, 3) * 0.013).as_matrix()
    positions, rotations = target.rest_positions.copy() * 0.8, target.rest_rotations.copy()
    rest_rotations = Rotation.from_quat(rotations).as_matrix()
    for i, parent in enumerate(target.parents):
        parent_basis = bases[parent] if parent >= 0 else np.eye(3)
        positions[i] = parent_basis.T @ positions[i]
        rotations[i] = Rotation.from_matrix(parent_basis.T @ rest_rotations[i] @ bases[i]).as_quat()
    rotations[0] = (Rotation.from_euler("y", -90, degrees=True) * Rotation.from_quat(rotations[0])).as_quat()
    skeleton = NativeSkeleton([inverse.get(n, n) for n in target.names], target.parents.copy(), positions,
                              rotations, target.rest_scales.copy())
    motion = rest_motion(skeleton, SourceInfo("fixture", {}, "analytic"))
    motion = SampledMotion(skeleton, np.arange(3) / 30, np.repeat(motion.positions, 3, axis=0),
                           np.repeat(motion.rotations, 3, axis=0), np.repeat(motion.scales, 3, axis=0),
                           np.ones((count, 3), dtype=bool), motion.source, 2 / 30, 30, 0)
    mapping["source_signatures"] = [skeleton_signature(skeleton)]
    return target, mapping, motion


class SomaRetargetTests(unittest.TestCase):
    def test_parameterized_retarget(self):
        for case in ("bind_axes", "root_turn", "bvh_masks", "positive_scale"):
            with self.subTest(case=case):
                target, mapping, source = fixture()
                if case == "root_turn":
                    yaw = Rotation.from_euler("y", [[0], [45], [90]], degrees=True)
                    source.rotations[:, 0] = (yaw * Rotation.from_quat(source.rotations[:, 0])).as_quat()
                    source.positions[:, 0] += [[0, 0, 0], [0, 0.2, 0.1], [0, 0.4, 0.2]]
                converter = SomaRetargeter(source, target, mapping)
                if case == "positive_scale":
                    scaled = copy.deepcopy(source)
                    scaled.scales[1, 14, 0] = 1.2
                    result, metrics = converter.retarget(scaled)
                    np.testing.assert_allclose(result.scales, 1)
                    np.testing.assert_allclose(result.positions[:, 2:], np.broadcast_to(target.rest_positions[2:], result.positions[:, 2:].shape))
                    self.assertAlmostEqual(metrics["source_max_scale_deviation"], 0.2)
                    self.assertLess(metrics["max_bone_direction_sine_error"], 1e-8)
                    continue
                result, metrics = converter.retarget(source)
                self.assertEqual(skeleton_signature(result.skeleton), skeleton_signature(target))
                np.testing.assert_array_equal(source.timestamps, result.timestamps)
                self.assertLess(metrics["max_bone_direction_sine_error"], 1e-8)
                actual = world_positions(result)
                source_world = world_positions(source)
                expected = source_world @ converter.alignment.T / 0.8
                if case == "bind_axes":
                    reference = world_positions(rest_motion(target, source.source))[0]
                    np.testing.assert_allclose(actual, np.broadcast_to(reference, actual.shape), atol=1e-8)
                elif case == "root_turn":
                    # 全局刚体运动应被完整迁移到 Hips，SOMA Root 保持静止。
                    np.testing.assert_allclose(actual[:, 1:], expected[:, 1:], atol=1e-8)
                    np.testing.assert_allclose(result.positions[:, 0], 0, atol=1e-12)
                else:
                    with tempfile.TemporaryDirectory() as temporary:
                        self.assertLess(write_bvh(Path(temporary) / "test.bvh", result), 1e-7)
                    for i, name in enumerate(target.names):
                        if name not in mapping["joints"]:
                            self.assertFalse(result.track_mask[i].any())
                    self.assertFalse(result.source.details["retarget"]["training_ready"])


if __name__ == "__main__":
    unittest.main()
