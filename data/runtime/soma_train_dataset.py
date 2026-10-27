"""把 SOMA processed 运动转换为训练/评测共用的四键 NPZ 与 Schema v3 clip。

设计要点（与 docs/soma-training-execution-plan.md 对齐）：

1. SOMA 的 Root 节点是固定的，完整运动合并在 Hips 上。训练契约要求的
   `root_track` 是独立的权威 Root，因此这里从 Hips 合成一条可逆的 Root 代理：
   水平位置与朝向取自 Hips，垂直高度全部留在身体姿态中。
2. 该 Root 代理是**离线派生**，不是场景观测到的 Actor Root，输出中显式标注。
3. 输出四键 `positions / rotations / timestamps / root_track`，与
   `data.runtime.conditioned_motion.load_raw` 的硬性要求一致，供训练与评测共用。
4. 每条 clip 记录 `fps` 与 `fps_group`；30fps 与 120fps 不混进同一 batch。
"""

import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.motion_kinematics import world_positions
from data.runtime.native_motion_io import read_sampled_motion, skeleton_signature

# 基准帧率：其他帧率的 down_t 由它推导
BASE_FPS = 120.00480019200768
# 四个场景。D 是开放交互组（手势/舞蹈/物体操作等），参与训练以提升泛化，
# 但不用于动作合成的硬约束。窗口与 A 相同：动作尺度接近，且数据量最大。
SCENE_DOWN_T = {"A": 4, "B": 3, "C": 3, "D": 4}
# 场景窗口（秒），统一按秒定义
SCENE_WINDOW_SECONDS = {"A": 0.4, "B": 0.6, "C": 0.6, "D": 0.4}
HISTORY_SECONDS = 0.2


def fps_group(fps, tolerance=1e-3):
    """把帧率归到组名；只接受可被基准帧率整除为 2 的幂次的帧率。"""
    ratio = BASE_FPS / float(fps)
    power = int(round(np.log2(ratio)))
    if abs(2.0 ** power - ratio) > tolerance * max(ratio, 1.0):
        raise ValueError(f"帧率 {fps} 不是基准帧率 {BASE_FPS} 的 2 的幂次分之一，无法用 down_t 对齐")
    return f"fps{int(round(float(fps)))}", power


def down_t_for(scene, fps):
    """场景在给定帧率下的 down_t：基准帧率的 down_t 减去对齐所需的幂次。"""
    _, power = fps_group(fps)
    value = SCENE_DOWN_T[scene] - power
    if value < 0:
        raise ValueError(f"场景 {scene} 在 {fps}fps 下 down_t 为负，该帧率对本场景不可用")
    return value


def frames_per_token(scene, fps):
    return 2 ** down_t_for(scene, fps)


def window_frames(scene, fps):
    return int(round(SCENE_WINDOW_SECONDS[scene] * float(fps)))


def history_frames(fps):
    return int(round(HISTORY_SECONDS * float(fps)))


def token_budget(scene, fps, reference_frames=4):
    """返回该场景的 token 占用，用于在构建前检查是否超过模型上限。"""
    fpt = frames_per_token(scene, fps)
    window = window_frames(scene, fps) / fpt
    history = history_frames(fps) / fpt
    return {"scene": scene, "fps": float(fps), "down_t": down_t_for(scene, fps),
            "frames_per_token": fpt, "window_tokens": window, "history_tokens": history,
            "reference_tokens": reference_frames,
            "total": window + history + reference_frames}


def derive_root_track(motion, roles):
    """从 Hips 合成可逆的独立 Root：水平位移与朝向来自 Hips，身高留在身体姿态。

    返回 [T, 7] 的米制 Motion 空间轨迹（xyz + xyzw）。
    """
    names = motion.skeleton.names
    pelvis = names.index(roles["pelvis"])
    world = world_positions(motion)
    hips = world[:, pelvis]

    # 朝向：以 Hips 局部旋转的偏航为人体朝向；水平 Root 只取偏航，俯仰/横滚留在身体姿态。
    quaternions = motion.rotations[:, pelvis]
    rotation = Rotation.from_quat(quaternions)
    # as_euler 返回 [T, 3]；取 Y 分量（偏航）并保持列向量形状。
    yaw = rotation.as_euler("YZX", degrees=False)[:, [0]]
    heading = Rotation.from_euler("Y", yaw)
    quat = heading.as_quat()

    positions = hips.copy()
    positions[:, 1] = 0.0  # 垂直高度不进入 Root 代理，全部留在身体姿态
    track = np.concatenate([positions, quat], axis=-1).astype(np.float64)
    return track, {"root_policy": "derived_from_hips_horizontal_only",
                   "root_status": "offline_proxy_not_observed_actor_root",
                   "vertical_kept_in_body": True}


def to_training_npz(motion, roles, *, expected_hashes=None):
    """读取 sampled motion，产出四键数组与元信息，但不落盘。"""
    if expected_hashes is not None:
        from data.runtime.native_motion_io import read_sampled_motion as _read
        motion = _read(Path(motion), file_hashes=expected_hashes) if isinstance(motion, (str, Path)) else motion

    world = world_positions(motion)
    root_track, root_meta = derive_root_track(motion, roles)
    root_index = motion.root_joint if motion.root_joint is not None else 0

    # 训练契约的 positions 是 Root 相对姿态：用合成 Root 的逆变换把世界坐标搬回 Root 局部。
    # scipy 的 Rotation.apply 需要每个向量配一个旋转，因此把 [T] 旋转变换为 [T,1] 再广播。
    rotation = Rotation.from_quat(root_track[:, 3:])
    matrices = rotation.as_matrix()                     # [T, 3, 3]
    local = world - root_track[:, None, :3]             # [T, J, 3]
    relative = np.einsum("tji,tkj->tki", matrices, local)  # R^T · (p - t)

    # 若骨架自带固定 Root 节点，把它并入 Hips 相对表示（Root 由 root_track 单独提供）。
    positions = relative.astype(np.float64)
    rotations = Rotation.from_quat(motion.rotations.reshape(-1, 4)).as_matrix().reshape(
        motion.rotations.shape[0], motion.rotations.shape[1], 3, 3)

    return {
        "positions": positions,
        "rotations": rotations,
        "timestamps": np.asarray(motion.timestamps, dtype=np.float64),
        "root_track": root_track,
        "fps": float(motion.fps),
        "source_duration": float(motion.source_duration),
        "skeleton_signature": skeleton_signature(motion.skeleton),
        "source_id": motion.source.source_id,
        "root_meta": root_meta,
        "root_index": int(root_index),
    }


def write_npz(destination, arrays):
    """写四键 NPZ；先写临时文件再原子替换，避免半成品被当成完整样本。"""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, positions=arrays["positions"], rotations=arrays["rotations"],
                            timestamps=arrays["timestamps"], root_track=arrays["root_track"])
        stream.flush()
    temporary.replace(destination)


def build_clip(source_folder, index_row, roles, *, scene):
    """把一个 SOMA processed clip 变成 Schema v3 记录（不落盘）。"""
    source_folder = Path(source_folder)
    motion = read_sampled_motion(source_folder)
    arrays = to_training_npz(motion, roles)
    fps = arrays["fps"]
    group, power = fps_group(fps)
    frames = len(arrays["timestamps"])
    needed = window_frames(scene, fps) + history_frames(fps)

    record = {
        "source_id": arrays["source_id"],
        "raw_file": str(source_folder),
        "source_frames": frames,
        "fps": fps,
        "fps_group": group,
        "fps_power": power,
        "scene": scene,
        "down_t": down_t_for(scene, fps),
        "frames_per_token": frames_per_token(scene, fps),
        "window_frames": window_frames(scene, fps),
        "history_frames": history_frames(fps),
        "min_frames_required": needed,
        "usable": frames >= needed,
        "skeleton_signature": arrays["skeleton_signature"],
        **arrays["root_meta"],
    }
    if not record["usable"]:
        record["reject_reason"] = f"帧数 {frames} 不足 {needed}（窗口+历史）"
    return record, arrays
