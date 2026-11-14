"""从已知 SOMA 参考资产冻结骨架；不处理动作轨迹、时间轴或训练表示。"""

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.bvh_reader import read_bvh
from data.runtime.native_motion import NativeSkeleton, validate_skeleton
from data.runtime.native_motion_io import skeleton_signature


def _reference_array(text, name):
    # 仅提取本项目已知 ASCII 参考资产的静态数组，不作为通用 USD 导入器。
    match = re.search(r"\b" + name + r"\s*=\s*(\[[^\n]+\])", text)
    if match is None:
        raise ValueError(f"SOMA 参考 USD 缺少 {name}")
    return ast.literal_eval(match.group(1))


def build_contract(reference_directory):
    folder = Path(reference_directory)
    usd_path = folder / "soma_base_skel_minimal.usd"
    bvh_path = folder / "soma_base_skel_minimal.bvh"
    usd_bytes, bvh_bytes = usd_path.read_bytes(), bvh_path.read_bytes()
    text = usd_bytes.decode("utf-8")
    if len(re.findall(r'\bdef Skeleton\s+"', text)) != 1:
        raise ValueError("SOMA 参考 USD 必须恰好包含一个 Skeleton")
    units = float(re.search(r"\bmetersPerUnit\s*=\s*([0-9.eE+-]+)", text).group(1))
    up_axis = re.search(r'\bupAxis\s*=\s*"([XYZ])"', text).group(1)
    if units != 0.01 or up_axis != "Y":
        raise ValueError("SOMA 参考资产的单位或向上轴发生变化")
    paths = _reference_array(text, "joints")
    names = [path.rsplit("/", 1)[-1] for path in paths]
    parents = np.asarray([paths.index(path.rsplit("/", 1)[0]) if "/" in path else -1 for path in paths], dtype=np.int64)
    # USD 使用行向量矩阵；本项目局部 TRS 使用列向量组合。
    local = np.asarray(_reference_array(text, "restTransforms"), dtype=np.float64).transpose(0, 2, 1)
    if len(names) != 78 or local.shape != (78, 4, 4):
        raise ValueError("SOMA 参考骨架必须包含 78 个节点及完整局部参考变换")
    scales = np.linalg.norm(local[:, :3, :3], axis=1)
    matrices = local[:, :3, :3] / scales[:, None, :]
    if not np.allclose(matrices.transpose(0, 2, 1) @ matrices, np.eye(3), atol=1e-10):
        raise ValueError("参考变换包含未支持的剪切")
    if not np.allclose(np.linalg.det(matrices), 1, atol=1e-10):
        raise ValueError("参考旋转不是正交旋转")
    quaternions = Rotation.from_matrix(matrices).as_quat()
    quaternions[quaternions[:, 3] < 0] *= -1
    skeleton = NativeSkeleton(names, parents, local[:, :3, 3] * units, quaternions, scales)
    validate_skeleton(skeleton)
    world = local.copy()
    for joint, parent in enumerate(parents):
        if parent >= 0:
            world[joint] = world[parent] @ local[joint]
    bvh = read_bvh(bvh_path)
    if bvh.names != names or not np.array_equal(bvh.parents, parents):
        raise ValueError("USD 与参考 BVH 的关节顺序或层级不一致")
    bvh_positions, bvh_rotations = bvh.forward()
    if not np.allclose(bvh_positions, world[None, :, :3, 3], atol=1e-8, rtol=0):
        raise ValueError("专用参考 BVH 与 USD 参考位置不一致")
    if not np.allclose(bvh_rotations, world[None, :, :3, :3], atol=1e-10, rtol=0):
        raise ValueError("专用参考 BVH 与 USD 参考旋转不一致")
    point = {name: world[i, :3, 3] for i, name in enumerate(names)}
    if not (point["LeftHand"][0] > point["RightHand"][0]
            and point["HeadEnd"][1] > point["Hips"][1] > point["LeftFoot"][1]
            and all(point[side + "ToeBase"][2] > point[side + "Foot"][2] for side in ("Left", "Right"))):
        raise ValueError("参考姿态的左右、向上或足部前向不符合 Motion 轴定义")
    roles = {"root": "Root", "pelvis": "Hips", "left_hip": "LeftLeg", "right_hip": "RightLeg",
             "left_knee": "LeftShin", "right_knee": "RightShin", "left_foot": "LeftFoot", "right_foot": "RightFoot",
             "left_toe": "LeftToeBase", "right_toe": "RightToeBase", "left_hand": "LeftHand", "right_hand": "RightHand",
             "left_shoulder": "LeftShoulder", "right_shoulder": "RightShoulder", "head": "Head", "jaw": "Jaw",
             "left_eye": "LeftEye", "right_eye": "RightEye"}
    if names[0] != "Root" or names[1] != "Hips" or parents[1] != 0 or np.count_nonzero(parents < 0) != 1:
        raise ValueError("SOMA Root/Hips 参考层级发生变化")
    document = {"schema": "motionweaver_skeleton", "version": 1, "id": "soma_uniform_v1",
                "scope": "reference_skeleton_only", "coordinate_system": "motion_m_y_up_z_forward_xyzw",
                "source_space": {"meters_per_unit": units, "up_axis": up_axis, "forward_axis": "+Z",
                                 "left_axis": "+X", "basis_to_motion": np.eye(3).tolist()},
                "reference_policy": "usd_local_rest_transforms_verified_by_dedicated_reference_bvh",
                "source_files": {path.name: hashlib.sha256(payload).hexdigest()
                                 for path, payload in ((usd_path, usd_bytes), (bvh_path, bvh_bytes))},
                "joint_count": len(names), "root_joint": 0, "pelvis_joint": 1,
                "body_joint_indices": list(range(1, len(names))),
                "named_end_joint_indices": [i for i, name in enumerate(names) if name.endswith("End")],
                "roles": roles, "skeleton_signature": skeleton_signature(skeleton),
                "skeleton": {"names": names, "parents": parents.tolist(), "rest_positions": skeleton.rest_positions.tolist(),
                             "rest_rotations": quaternions.tolist(), "rest_scales": scales.tolist()},
                "deferred_decisions": ["root_hips_motion_policy", "time_sampling_policy"]}
    audit = {"joints": len(names), "body_nodes": len(names) - 1,
             "reference_bvh_frames_checked": len(bvh.values),
             "reference_position_max_error_cm": float(np.linalg.norm(bvh_positions - world[None, :, :3, 3], axis=-1).max()),
             "reference_rotation_max_matrix_error": float(np.abs(bvh_rotations - world[None, :, :3, :3]).max()),
             "unit_scale_max_deviation": float(np.abs(scales - 1).max())}
    return document, audit


def write_contract(path, document):
    path = Path(path)
    payload = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError("已冻结目标不可原地修改，请升级目标版本")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
    return hashlib.sha256(payload).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-directory", type=Path, default=Path("E:/MotionWeaverData/soma_shapes/soma_base_rig"))
    parser.add_argument("--output", type=Path, default=Path("data/skeletons/soma_uniform_v1.json"))
    args = parser.parse_args()
    document, audit = build_contract(args.reference_directory)
    digest = write_contract(args.output, document)
    print(json.dumps(dict(audit, contract_sha256=digest, skeleton_signature=document["skeleton_signature"], output=str(args.output)), ensure_ascii=False))


if __name__ == "__main__":
    main()
