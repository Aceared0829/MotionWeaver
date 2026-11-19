"""RefPose 数据语义的事实清单（不是推测，全部可由独立判据复算）。

为什么单独一个模块：UE 侧 C++ 装配输入、反解输出时必须知道这套数据的
真实语义。之前文档里对同一件事有两种说法（root_local / global），
继续让每个读者各自解读就会分叉。这里把「已证实」「未证实」分开写清楚。

已证实（用 frozen 数据跑判据，见 tools/verify_refpose_ue_contract.py）：

1. `p.npy` 是**每帧、Root 局部、米制、Y-up** 的骨骼位置。
   判据：逐骨「子-父距离」标准差 ≤ 4.6e-8 m（77 根骨，Hips 除外见下）。
   这不是「用 p 推出的结论」——刚性是位置数据自身的内禀性质。
2. `p[:, 0]` 与权威 Root 轨道 `k` 满足 `p_root = -R(k)^T · k_pos`，误差 5.5e-7 m。
   判据：manifest 的 `root.p_axis0` 声明，独立复算。
3. `r.npy` 解码后是单位正交矩阵（偏差 1.7e-7），且与 `arrays.npz`
   的 `rotations` 的 6D 编码逐元素一致（8.3e-8）。
4. Hips→Root 距离**不恒定**（std 2.25 m）：Root 是独立轨道，不是 Hips 的父级骨长链。
   因此「用骨长刚性重建全身」必须把 Root 单独处理。

未证实（U1，禁止在未解决前依赖）：

- `r` 与 `p` 的骨长方向是否同源。用「单子骨方向 vs r 的列」判据，
  中位误差 0.22（0 为完全一致），说明 r 至少不是「直接用某一列表达骨方向」。
- 因此**不能**用 `r` 做 FK 去重建位置，也**不能**用位置反推 r。

对 UE 侧的直接约束：

- 蒙皮必须让 UE 骨架自身的静止骨长说了算；模型给的位置用来驱动方向与局部姿态，
  不直接写绝对位置，否则骨长会被数据里的 Root 轨道污染。
- 位置与旋转要分别验证，不共享一个「总误差」结论。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

# 模型内部坐标（前 +Z、上 +Y、右 -X）<-> UE（前 +X、上 +Z、右 +Y）
UE_TO_MOTION = np.array([[0.0, -1.0, 0.0],
                         [0.0, 0.0, 1.0],
                         [1.0, 0.0, 0.0]], dtype=np.float64)
MOTION_TO_UE = np.linalg.inv(UE_TO_MOTION)

DECODE_COL0 = (0, 2, 4)
DECODE_COL1 = (1, 3, 5)


def decode_6d(values: np.ndarray) -> np.ndarray:
    """row-major 交错 6D -> 3x3。返回 [..., 3, 3]。"""
    col0 = np.asarray(values)[..., list(DECODE_COL0)]
    col1 = np.asarray(values)[..., list(DECODE_COL1)]
    return np.stack((col0, col1, np.cross(col0, col1)), axis=-1)


def encode_6d(matrices: np.ndarray) -> np.ndarray:
    """3x3 -> row-major 交错 6D，与 decode_6d 互逆。"""
    matrices = np.asarray(matrices)
    return np.stack(
        (matrices[..., 0, 0], matrices[..., 0, 1],
         matrices[..., 1, 0], matrices[..., 1, 1],
         matrices[..., 2, 0], matrices[..., 2, 1]), axis=-1)


def root_local(track: np.ndarray, origin: np.ndarray) -> np.ndarray:
    """位置与朝向都相对 origin；与 inference/export/refpose/inputs.py 同定义。"""
    from scipy.spatial.transform import Rotation

    rotation = Rotation.from_quat(np.asarray(origin[3:]))
    positions = rotation.inv().apply(np.asarray(track)[:, :3] - np.asarray(origin)[:3])
    rotations = (rotation.inv() * Rotation.from_quat(np.asarray(track)[:, 3:])).as_quat()
    return np.concatenate((positions, rotations), axis=-1)


def load_skeleton(path: Path) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    """读取骨架契约，返回 (骨名, 父索引, 参考位置, 参考四元数)。

    注意：skeleton.json 的 `position/rotation` 是**参考姿态**，
    不是逐帧数据，也不是与 p 同源的逐帧量。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    names = [bone["name"] for bone in data["bones"]]
    parents = np.asarray([bone["parent"] for bone in data["bones"]], dtype=np.int64)
    rest_pos = np.asarray([bone["position"] for bone in data["bones"]], dtype=np.float64)
    rest_quat = np.asarray([bone["rotation"] for bone in data["bones"]], dtype=np.float64)
    return names, parents, rest_pos, rest_quat


def bone_rigidity(positions: np.ndarray, parents: np.ndarray) -> np.ndarray:
    """逐骨的「与父骨距离」标准差，单位与 positions 相同。Root 用 nan 占位。"""
    positions = np.asarray(positions, dtype=np.float64)
    out = np.full(len(parents), np.nan)
    for index in range(len(parents)):
        parent = int(parents[index])
        if parent < 0:
            continue
        distance = np.linalg.norm(positions[:, index] - positions[:, parent], axis=-1)
        out[index] = float(distance.std())
    return out


def root_axis0_check(positions: np.ndarray, root_track: np.ndarray) -> float:
    """复算 manifest 的 `p_root == -R^T · k_pos`，返回最大绝对误差（米）。"""
    from scipy.spatial.transform import Rotation

    rotation = Rotation.from_quat(np.asarray(root_track)[:, 3:]).as_matrix()
    predicted = -np.einsum("tji,tj->ti", rotation, np.asarray(root_track)[:, :3])
    return float(np.abs(np.asarray(positions)[:, 0] - predicted).max())
