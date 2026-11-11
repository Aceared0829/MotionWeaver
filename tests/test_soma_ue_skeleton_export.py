"""锁住 SOMA 骨架转 UE 的四条约定。

这些断言不是形式检查：每一条都对应 UE 侧会静默出错的一种情形。
手性错了 → 角色左右反；单位错了 → 角色 1 cm 高或 100 m 高；
父索引没排序 → UE 建骨架时静默丢骨；四元数没归一 → 骨骼扭曲。
"""
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inference.export.export_soma_skeleton import (
    C,
    build_payload,
    load_skeleton,
    local_matrices,
    world_matrices,
)

SKELETON = ROOT / "data" / "skeletons" / "soma_uniform_v1.json"


@pytest.fixture(scope="module")
def exported():
    names, parents, positions, rotations = load_skeleton(SKELETON)
    payload, world_ue = build_payload(names, parents, positions, rotations, SKELETON)
    return names, parents, positions, rotations, payload, world_ue


def test_basis_is_a_mirror_not_a_rotation():
    """C 的行列式必须是 -1：这是手性翻转，不是旋转。"""
    assert float(np.linalg.det(C)) == -1.0


def test_bone_count_and_order(exported):
    names, parents, _positions, _rotations, payload, _world = exported
    bones = payload["bones"]
    assert len(bones) == 78
    assert [b["name"] for b in bones] == names
    assert [b["parent_index"] for b in bones] == parents
    assert parents[0] == -1, "Root 必须是唯一的无父骨"


def test_quaternions_are_unit(exported):
    _n, _p, _pos, _rot, payload, _w = exported
    norms = [float(np.linalg.norm(b["rotation_quat_xyzw"])) for b in payload["bones"]]
    assert max(abs(n - 1.0) for n in norms) < 1e-9


def test_hips_are_about_one_metre_up(exported):
    _n, _p, _pos, _rot, payload, _w = exported
    hips = next(b for b in payload["bones"] if b["name"] == "Hips")
    z = hips["translation_cm"][2]
    assert 95.0 < z < 110.0, "Hips 的 Z 应在 1 米量级；单位或轴换错会立刻越界"


def test_left_side_lands_on_minus_y(exported):
    """UE 里角色面朝 +X 时左在 -Y。这条错了就是左右反。"""
    _n, _p, _pos, _rot, payload, _w = exported
    by_name = {b["name"]: b for b in payload["bones"]}
    for left, right in (("LeftArm", "RightArm"), ("LeftFoot", "RightFoot"), ("LeftHand", "RightHand")):
        assert by_name[left]["translation_cm"][1] < by_name[right]["translation_cm"][1], left + " 应小于 " + right


def test_roundtrip_rebuilds_world(exported):
    """用产物反解回世界矩阵，必须还原 UE 世界矩阵。"""
    _n, parents, _pos, _rot, payload, world_ue = exported
    local = np.tile(np.eye(4), (len(parents), 1, 1))
    for index, bone in enumerate(payload["bones"]):
        local[index, :3, :3] = Rotation.from_quat(bone["rotation_quat_xyzw"]).as_matrix()
        local[index, :3, 3] = np.asarray(bone["translation_cm"], dtype=np.float64) / 100.0
    assert float(np.abs(world_matrices(parents, local) - world_ue).max()) < 1e-6


def test_bone_lengths_are_preserved(exported):
    """C 是保长的；这条挡住单位换算写错（例如漏乘 100）。"""
    _n, _p, positions, rotations, payload, _w = exported
    source = local_matrices(positions, rotations)
    exported_len = np.array([np.linalg.norm(b["translation_cm"]) for b in payload["bones"]]) / 100.0
    source_len = np.linalg.norm(source[:, :3, 3], axis=1)
    assert float(np.abs(exported_len - source_len).max()) < 1e-9
