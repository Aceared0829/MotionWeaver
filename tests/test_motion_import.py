"""独立数据入口的四组参数化验收：FK、glTF 时间、共享存储及拒绝发布。"""

import base64
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.bvh_reader import read_bvh
from data.runtime.importers.bvh import read_bvh_motion
from data.runtime.importers.gltf import read_gltf_motion
from data.runtime.native_motion_io import (
    read_native_motion,
    read_sampled_motion,
    write_native_motion,
)
from data.runtime.normalize_motion import normalize_motion
from data.tools.ingest.import_motion_dataset import project_manifest, run_batch


def bvh_fixture(folder):
    path = folder / "walk.bvh"
    path.write_text("""HIERARCHY
ROOT Root {
 OFFSET 0 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
 JOINT Hips {
  OFFSET 1 0 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
  End Site { OFFSET 1 0 0 }
 }
}
MOTION
Frames: 3
Frame Time: 0.5
10 20 30 90 0 0 2 0 0 0 0 0
11 20 30 90 0 0 2 0 0 0 0 0
12 20 30 90 0 0 2 0 0 0 0 0
""", encoding="utf-8")
    return path


def gltf_fixture(folder):
    # 两个通道具有不同时间轴，父节点的参考位移必须保留。
    arrays = [np.array([0, 1], dtype="<f4"), np.array([[0, 1, 0], [0, 3, 0]], dtype="<f4"),
              np.array([0, 0.5, 1], dtype="<f4"), np.array([[0, 0, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], dtype="<f4")]
    payload = b"".join(array.tobytes() for array in arrays)
    offset, views = 0, []
    for array in arrays:
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": array.nbytes})
        offset += array.nbytes
    document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(payload), "uri": "data:application/octet-stream;base64," + base64.b64encode(payload).decode()}],
                "bufferViews": views, "accessors": [{"bufferView": i, "componentType": 5126, "count": len(a), "type": t}
                                                   for i, (a, t) in enumerate(zip(arrays, ["SCALAR", "VEC3", "SCALAR", "VEC4"]))],
                "nodes": [{"name": "Scene", "children": [1], "translation": [4, 0, 0]}, {"name": "Hips"}], "skins": [{"joints": [1]}],
                "animations": [{"samplers": [{"input": 0, "output": 1}, {"input": 2, "output": 3, "interpolation": "STEP"}],
                                "channels": [{"sampler": 0, "target": {"node": 1, "path": "translation"}},
                                             {"sampler": 1, "target": {"node": 1, "path": "rotation"}}]}]}
    path = folder / "motion.gltf"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class MotionImportTests(unittest.TestCase):
    def test_parameterized_import_contract(self):
        for case in ("bvh_fk", "gltf_time", "shared_batch", "reject_additive"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                if case == "gltf_time":
                    sampled = normalize_motion(read_gltf_motion(gltf_fixture(folder), 0), fps=4)
                    np.testing.assert_allclose(sampled.positions[:, 1, 1], [1, 1.5, 2, 2.5, 3])
                    np.testing.assert_allclose(sampled.positions[:, 0, 0], 4)
                    np.testing.assert_allclose(sampled.rotations[1, 1], [0, 0, 0, 1])
                    np.testing.assert_allclose(sampled.rotations[2, 1], [0, 0, 1, 0])
                    np.testing.assert_array_equal(sampled.track_mask, [[False, False, False], [True, True, False]])
                    continue
                path = bvh_fixture(folder)
                profile = {"meters_per_unit": 0.01, "basis": [[0, -1, 0], [0, 0, 1], [1, 0, 0]], "root_joint": "Root"}
                native = read_bvh_motion(path, profile)
                if case == "bvh_fk":
                    sampled = normalize_motion(native, fps=2)
                    rotation = Rotation.from_quat(sampled.rotations[:, 0]).as_matrix()
                    child_world = sampled.positions[:, 0] + np.einsum("tij,tj->ti", rotation, sampled.positions[:, 1])
                    original, _ = read_bvh(path).forward()
                    expected = original[:, 1] @ np.asarray(profile["basis"]).T * 0.01
                    np.testing.assert_allclose(child_world, expected, atol=1e-10)
                    np.testing.assert_allclose(sampled.skeleton.rest_positions[1], [0, 0, 0.01])
                    self.assertEqual(sampled.root_joint, 0)
                elif case == "shared_batch":
                    project = folder / "SampleProject"
                    (project / "Content/Other").mkdir(parents=True)
                    (project / "SampleProject.uproject").write_text("{}", encoding="utf-8")
                    (project / "Content/Other/Walk.uasset").touch()
                    scanned = project_manifest(project)
                    self.assertEqual(scanned["scan"]["candidates"], 1)
                    self.assertEqual(scanned["clips"][0]["asset"], "SampleProject/Content/Other/Walk.Walk")
                    second = folder / "run.bvh"
                    second.write_bytes(path.read_bytes())
                    manifest = {"dataset": "fixture", "clips": [{"format": "bvh", "path": str(p), "profile": profile} for p in (path, second)]}
                    result = run_batch(manifest, folder / "storage", "batch", fps=2)
                    self.assertEqual(result["normalized"], 2)
                    for partition in ("raw", "processed"):
                        self.assertEqual(len(list((folder / "storage" / partition / "fixture/batch/skeletons").iterdir())), 1)
                    first = result["results"][0]
                    restored = read_native_motion(first["raw"])
                    np.testing.assert_array_equal(restored.tracks[0].positions, native.tracks[0].positions)
                    sampled = read_sampled_motion(first["processed"])
                    self.assertEqual(sampled.positions.shape, (3, 2, 3))
                    with self.assertRaises(FileExistsError):
                        run_batch(manifest, folder / "storage", "batch", fps=2)
                else:
                    native.additive = "local_delta"
                    write_native_motion(folder / "additive", native)
                    manifest = {"dataset": "fixture", "clips": [{"format": "native", "path": str(folder / "additive")}]}
                    result = run_batch(manifest, folder / "storage", "rejected", fps=2)
                    self.assertEqual(result["failed"], 1)
                    self.assertFalse(Path(result["processed"]).exists())
                    self.assertTrue((Path(result["results"][0]["raw"]) / "motion.json").exists())


if __name__ == "__main__":
    unittest.main()
