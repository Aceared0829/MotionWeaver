"""固定 SOMA 骨架的四组契约验收，不依赖 E 盘或 UE。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from data.runtime.native_motion import (
    NativeMotion,
    NativeTrack,
    SourceInfo,
    SourceSpace,
)
from data.runtime.native_motion_io import (
    read_native_motion,
    skeleton_signature,
    write_native_motion,
)
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.ingest.freeze_soma_skeleton import write_contract

CONTRACT = Path(__file__).resolve().parents[1] / "data/skeletons/soma_uniform_v1.json"


class SomaSkeletonTests(unittest.TestCase):
    def test_parameterized_contract(self):
        for case in ("frozen_geometry", "changed_geometry", "immutable_version", "native_roundtrip"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                skeleton, document = load_skeleton_contract(CONTRACT)
                if case == "frozen_geometry":
                    self.assertEqual(len(skeleton.names), 78)
                    self.assertEqual(skeleton.names[:2], ["Root", "Hips"])
                    self.assertEqual(document["body_joint_indices"], list(range(1, 78)))
                    self.assertEqual(np.count_nonzero(skeleton.parents < 0), 1)
                    self.assertGreater(np.abs(skeleton.rest_rotations[:, :3]).max(), .1)
                    self.assertEqual(document["deferred_decisions"], ["root_hips_motion_policy", "time_sampling_policy"])
                    self.assertNotIn("fps", document)
                elif case == "changed_geometry":
                    document["skeleton"]["rest_positions"][1][1] += .01
                    target = folder / "changed.json"
                    target.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "签名不一致"):
                        load_skeleton_contract(target)
                elif case == "immutable_version":
                    target = folder / "frozen.json"
                    before = write_contract(target, document)
                    self.assertEqual(write_contract(target, document), before)
                    changed = copy.deepcopy(document)
                    changed["reference_policy"] = "changed"
                    with self.assertRaises(FileExistsError):
                        write_contract(target, changed)
                    self.assertEqual(write_contract(target, document), before)
                else:
                    tracks = [NativeTrack(i, np.array([0.]), skeleton.rest_positions[i:i + 1],
                                          np.array([0.]), skeleton.rest_rotations[i:i + 1],
                                          np.array([0.]), skeleton.rest_scales[i:i + 1]) for i in range(78)]
                    motion = NativeMotion(skeleton, tracks, 0., SourceSpace(1., np.eye(3)),
                                          SourceInfo("target_reference", {}, "frozen_skeleton_fixture"), root_joint=0)
                    output = folder / "data/clips/reference"
                    write_native_motion(output, motion, folder / "data/skeletons")
                    restored = read_native_motion(output)
                    self.assertEqual(skeleton_signature(restored.skeleton), document["skeleton_signature"])
                    np.testing.assert_array_equal(restored.skeleton.rest_rotations, skeleton.rest_rotations)
                    np.testing.assert_array_equal(restored.skeleton.rest_positions, skeleton.rest_positions)


if __name__ == "__main__":
    unittest.main()
