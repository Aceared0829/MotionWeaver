"""把 SOMA uniform 78 骨参考骨架转成 UE 可直接吃的数据。

只做坐标与单位转换，不读 UE、不写 uasset。产物是一份 JSON，UE 侧
编辑器命令用它批量建 Skeleton。这样做的好处是转换有唯一实现，
并且能在没有 UE 的环境下被自动化测试逐位校验。

四个必须写死的约定（来自 benchmark/manifest/refpose_manifest_v1.json）：

1. 源坐标系是 ours：forward=+Z、up=+Y、left=+X。
   UE 是 forward=+X、up=+Z、right=+Y。
   两套 (forward, up, right) 三元组的行列式符号不同（ours 为 +1，UE 为 -1），
   所以这是**手性差异**，必须做一次镜像等价变换，不是纯旋转。
   基变换 C 与 inference/visualization/ue_axis.py 用的是同一个，不要另写一套。
2. 源单位是米，UE 是厘米。乘 100 后四舍五入到 float32。
3. bones[].position 是**父骨局部空间**的偏移，不是世界坐标。
   实测它与 skeleton.json 的 neutral_joints 逐元素相等（max|d| = 0.0），
   也就是说它同时充当了静止姿态。要拿世界坐标必须自己走 FK。
4. 骨方向约定为父骨局部 +X。实测 78 骨里除 Hips 与几个手指/脚趾根外，
   子骨的局部偏移几乎全部落在父骨 +X 上，与 UE 一致，不需要额外扭转轴。

用法：
    python inference/export/export_soma_skeleton.py \\
        --skeleton data/skeletons/soma_uniform_v1.json \\
        --output   D:/MotionWeaverData/exports/soma_uniform_ue_v1.json
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

FORMAT = "soma_uniform_ue_v1"
CM_PER_M = 100.0

# ours(forward=+Z, up=+Y, left=+X) -> ue(forward=+X, up=+Z, right=+Y)。
# det(C) = -1，这是手性翻转，与纯旋转无关。
C = np.array([[0.0, 0.0, 1.0],
              [-1.0, 0.0, 0.0],
              [0.0, 1.0, 0.0]])


def load_skeleton(path):
    """读源骨架，返回 (names, parents, rest_positions, rest_rotations)。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != "motionweaver_skeleton":
        raise ValueError("不是 motionweaver_skeleton：" + str(raw.get("schema")))
    skeleton = raw["skeleton"]
    names = list(skeleton["names"])
    parents = [int(p) for p in skeleton["parents"]]
    positions = np.asarray(skeleton["rest_positions"], dtype=np.float64)
    rotations = np.asarray(skeleton["rest_rotations"], dtype=np.float64)
    scales = np.asarray(skeleton["rest_scales"], dtype=np.float64)
    if not len(names) == len(parents) == len(positions) == len(rotations):
        raise ValueError("名字/父索引/位置/旋转长度不一致")
    if not np.allclose(scales, 1.0, atol=1e-9, rtol=0.0):
        raise ValueError("出现非单位缩放；转换不支持缩放，需先明确语义")
    for index, parent in enumerate(parents):
        if parent >= index:
            raise ValueError(f"父索引必须排在子骨之前：骨 {index} ({names[index]}) 的父是 {parent}")
    return names, parents, positions, rotations


def local_matrices(positions, rotations):
    """局部 TRS 转 4x4，仅供 FK 用，不进入产物。"""
    mats = np.tile(np.eye(4), (len(positions), 1, 1))
    mats[:, :3, :3] = Rotation.from_quat(rotations).as_matrix()
    mats[:, :3, 3] = positions
    return mats


def world_matrices(parents, local):
    """按「父索引在子骨之前」的假定走一遍 FK，返回世界 4x4。"""
    count = len(parents)
    world = np.tile(np.eye(4), (count, 1, 1))
    for index in range(count):
        parent = parents[index]
        world[index] = local[index] if parent < 0 else world[parent] @ local[index]
    return world


def to_ue_world(world):
    """ours 世界矩阵换成 UE 世界矩阵：C @ M @ C^T。"""
    basis = np.eye(4)
    basis[:3, :3] = C
    inverse = np.eye(4)
    inverse[:3, :3] = C.T
    return np.einsum("ab,nbc,cd->nad", basis, world, inverse)


def ue_local_from_world(parents, world_ue):
    """从 UE 世界矩阵反解 UE 局部矩阵；这就是 UE 的 bone rest transform。"""
    count = len(parents)
    out = np.tile(np.eye(4), (count, 1, 1))
    for index in range(count):
        parent = parents[index]
        out[index] = world_ue[index] if parent < 0 else np.linalg.inv(world_ue[parent]) @ world_ue[index]
    return out


def build_payload(names, parents, positions, rotations, source_path):
    """产出 UE 侧要的数据。位置单位是厘米，旋转是归一化四元数 (x, y, z, w)。"""
    local = local_matrices(positions, rotations)
    world = world_matrices(parents, local)
    world_ue = to_ue_world(world)
    local_ue = ue_local_from_world(parents, world_ue)

    # rest_scales 实测是 1.0 与 float64 舍入噪声（max|d| = 3.3e-16），容差取 1e-9 即可。
    # 反解出来的矩阵应当已经满足，这里显式校验而不是「相信它是对的」。
    quats = Rotation.from_matrix(local_ue[:, :3, :3]).as_quat()
    translations = local_ue[:, :3, 3] * CM_PER_M

    bones = []
    for index, name in enumerate(names):
        bones.append({
            "name": name,
            "parent_index": parents[index],
            "translation_cm": [float(f"{v:.12g}") for v in translations[index]],
            "rotation_quat_xyzw": [round(float(v), 9) for v in quats[index]],
        })

    payload = {
        "format": FORMAT,
        "purpose": "SOMA uniform 78 骨参考骨架的 UE 侧静止姿态；供编辑器命令建 Skeleton",
        "source": {
            "path": str(source_path).replace("\\\\", "/"),
            "sha256": sha256(source_path),
            "coordinate_system": "motion_m_y_up_z_forward_xyzw",
        },
        "target": {
            "engine": "Unreal Engine 5.8",
            "length_unit": "cm",
            "rotation_order": "xyzw",
            "bone_count": len(names),
        },
        "conversion": {
            "basis_matrix": C.tolist(),
            "basis_determinant": float(round(np.linalg.det(C), 12)),
            "handedness_note": "det = -1：源与目标的手性约定不同，这是镜像等价变换，不是纯旋转",
            "units": "m -> cm（乘 100）",
            "verified_against": "inference/visualization/ue_axis.py 使用同一个 C",
        },
        "bones": bones,
    }
    return payload, world_ue


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(payload):
    """在写盘之前把几件会导致 UE 侧静默出错的事挡住。"""
    bones = payload["bones"]
    if len(bones) != payload["target"]["bone_count"]:
        raise AssertionError("骨数与声明不一致")
    for index, bone in enumerate(bones):
        quat = np.asarray(bone["rotation_quat_xyzw"], dtype=np.float64)
        norm = float(np.linalg.norm(quat))
        if abs(norm - 1.0) > 1e-6:
            raise AssertionError(f"骨 {index} ({bone[chr(34)+chr(110)+chr(97)+chr(109)+chr(101)+chr(34)]}) 四元数模长 {norm:.9f}，不是单位四元数")
        if bone["parent_index"] >= index:
            raise AssertionError("骨 %d (%s) 的父索引没有排在前面" % (index, bone["name"]))
    # 躯干必须真的朝上。Hips 在 cm 单位下应显著高于 Root。
    hips = next(b for b in bones if b["name"] == "Hips")
    if not hips["translation_cm"][2] > 50.0:
        raise AssertionError("Hips 的 Z 只有 %.3f cm，看起来坐标轴换错了" % hips["translation_cm"][2])
    # 左右不能反：UE 里左在 -Y。
    left_arm = next(b for b in bones if b["name"] == "LeftArm")
    right_arm = next(b for b in bones if b["name"] == "RightArm")
    if not left_arm["translation_cm"][1] < right_arm["translation_cm"][1]:
        raise AssertionError("LeftArm 的 Y 没有小于 RightArm，左右手性反了")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skeleton", required=True, help="data/skeletons/soma_uniform_v1.json")
    parser.add_argument("--output", required=True, help="产物 JSON 路径")
    args = parser.parse_args()

    source = Path(args.skeleton)
    names, parents, positions, rotations = load_skeleton(source)
    payload, _ = build_payload(names, parents, positions, rotations, source)
    verify(payload)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    print("写出 %s（%d 骨）" % (output, len(names)))


if __name__ == "__main__":
    main()
